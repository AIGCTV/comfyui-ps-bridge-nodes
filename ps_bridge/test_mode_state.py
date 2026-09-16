"""Local editor testing intent, discovery and ownership (testModeVersion 3).

All mutations share the parameter registry lock. No network I/O occurs under it.
Mode state is ephemeral; starting a server always starts with testing disabled.
"""
from __future__ import annotations
import copy
import uuid
from collections import OrderedDict
from .errors import require
from .json_codec import digest


class TestModeCoordinator:
    def __init__(self, sessions, targets):
        self.sessions, self.targets = sessions, targets
        self.lock = sessions.lock
        self.on_change = lambda state: None
        self.candidate = None
        self.resumable = None
        self.controller = None
        self.owner_online = False
        self.last_seen = 0
        self.messages = OrderedDict()
        self.state = dict(version=3, serverEpoch=sessions.epoch, modeId=None, modeRevision=0,
                          enabled=False, status="off", ownerClientId=None, target=None,
                          controllerConnected=False, error=None)

    def snapshot(self):
        with self.lock:
            self.expire()
            return copy.deepcopy(self.state)

    def _change(self, *, force=False, **changes):
        if not force and all(self.state[k] == v for k, v in changes.items()):
            return
        self.state.update(copy.deepcopy(changes))
        self.state["modeRevision"] += 1
        self.on_change(copy.deepcopy(self.state))

    def _clear_target(self):
        self.resumable = None
        for target in self.targets.targets.values():
            target["online"] = False
        for session in self.sessions.by_id.values():
            if session["scope"]["domain"] == "test":
                session["attachments"].clear()
        self.candidate = None

    def _identity(self, payload, *, revision=False):
        require(payload.get("serverEpoch") == self.state["serverEpoch"],
                "SESSION_EXPIRED", "Test mode server restarted")
        require(payload.get("modeId") == self.state["modeId"],
                "DRAFT_CHANGED", "Test mode was replaced")
        if revision:
            require(payload.get("expectedModeRevision") == self.state["modeRevision"],
                    "REVISION_CONFLICT", "Test mode changed; read its current state")

    def _owner(self, client_id):
        require(self.state["enabled"] and self.state["ownerClientId"] == client_id,
                "FORBIDDEN", "Only the active editor owns this test mode", status=403)

    def dispatch(self, client_id, role, kind, payload, message_id):
        with self.lock:
            self.expire()
            key, fingerprint = (client_id, message_id), digest([kind, payload])
            if key in self.messages:
                prior = self.messages[key]
                require(prior[0] == fingerprint, "ID_REUSE", "Test mode messageId was reused")
                # Replays acknowledge current state, never resurrect an obsolete target.
                return copy.deepcopy(self.state)
            if kind == "test.mode.set":
                require(role == "editor", "FORBIDDEN", "Only editors can enable testing", status=403)
                self._identity(payload, revision=True)
                if payload["enabled"]:
                    if self.state["enabled"]:
                        require(self.state["ownerClientId"] == client_id or
                                (payload.get("takeover") is True and not self.owner_online),
                                "CONTROLLER_IN_USE", "Another browser owns testing; close it or explicitly replace its offline mode")
                        if self.state["ownerClientId"] == client_id:
                            self.messages[key] = (fingerprint,)
                            return copy.deepcopy(self.state)
                    self._clear_target()
                    self.controller = None
                    self.owner_online, self.last_seen = True, self.targets.clock()
                    self._change(enabled=True, modeId=uuid.uuid4().hex, status="switching",
                                 ownerClientId=client_id, target=None, controllerConnected=False, error=None)
                else:
                    self._owner(client_id)
                    self._off()
            elif kind == "test.mode.stop":
                require(role == "controller", "FORBIDDEN", "Only controllers can request an exit", status=403)
                self._identity(payload)
                require(self.controller in (None, client_id), "CONTROLLER_IN_USE", "Another controller owns testing")
                self._off()
            else:
                require(role == "editor", "FORBIDDEN", "Only editors can announce test graphs", status=403)
                self._identity(payload, revision=kind != "test.heartbeat")
                self._owner(client_id)
                if kind == "test.target":
                    require(self.state["status"] == "switching", "DRAFT_CHANGED", "Invalidate the old draft before publishing")
                    require(self.candidate is None and digest(payload["scope"]) not in self.targets.targets,
                            "DRAFT_CHANGED", "Publish with a fresh draftRevision after invalidation")
                    self.targets.target(client_id, payload)
                    self.candidate = {"name": payload["name"], "rootGraphId": payload["rootGraphId"],
                                      "scope": copy.deepcopy(payload["scope"])}
                    self.owner_online, self.last_seen = True, self.targets.clock()
                    self._change(force=True)
                elif kind == "test.invalidate":
                    self._clear_target()
                    self._change(force=True, status=payload["status"], target=None, controllerConnected=False,
                                 error=payload.get("error"))
                elif kind == "test.heartbeat":
                    scope = payload.get("scope")
                    if scope is not None:
                        target = self.state["target"] or self.candidate
                        require(target and target["scope"] == scope, "DRAFT_CHANGED", "Heartbeat belongs to an old graph")
                        self.targets.heartbeat(client_id, scope)
                    self.owner_online, self.last_seen = True, self.targets.clock()
                else:
                    require(False, "MESSAGE_INVALID", "Unknown test mode operation")
            self.messages[key] = (fingerprint,)
            if len(self.messages) > 512:
                self.messages.popitem(last=False)
            return copy.deepcopy(self.state)

    def _off(self):
        self._clear_target()
        self.controller, self.owner_online = None, False
        self._change(enabled=False, modeId=None, status="off", ownerClientId=None,
                     target=None, controllerConnected=False, error=None)

    def check_scope(self, client_id, role, scope):
        """Candidate is writable only by its editor until the initial apply ACK."""
        with self.lock:
            self.expire()
            target = self.state["target"] or self.candidate
            require(self.state["enabled"] and target and target["scope"] == scope,
                    "TEST_TARGET_UNAVAILABLE", "This test graph is no longer active")
            if role != "editor":
                require(self.state["status"] == "ready", "TEST_TARGET_UNAVAILABLE", "Editor has not applied the initial snapshot")
            else:
                self._owner(client_id)
            if role == "controller":
                require(self.controller in (None, client_id), "CONTROLLER_IN_USE", "Another controller owns testing")

    def resume_editor(self, client_id, payload):
        """Reattach an unchanged disconnected draft through the existing API.

        This memory-only identity is cleared by OFF, invalidation and takeover.
        The normal graph validator and a fresh applied ACK still gate readiness.
        """
        with self.lock:
            self.expire()
            target = self.resumable
            if (not target or not self.state["enabled"] or self.state["status"] != "unavailable"
                    or client_id != self.state["ownerClientId"] or payload.get("scope") != target["scope"]):
                return None
            retained = self.targets.targets.get(digest(target["scope"]))
            if retained is None:
                return None
            # sessions.attach validates the frozen definition, owner, graphId,
            # draftRevision and UI before issuing any new attachment token.
            online, seen = retained["online"], retained["lastSeen"]
            retained.update(online=True, lastSeen=self.targets.clock())
            try:
                result = self.sessions.attach(client_id, "editor", payload)
            except Exception:
                retained.update(online=online, lastSeen=seen)
                raise
            self.candidate, self.resumable = copy.deepcopy(target), None
            self.owner_online, self.last_seen = True, self.targets.clock()
            self._change(status="switching", target=None, controllerConnected=False, error=None)
            return result

    def attached(self, client_id, role, scope):
        with self.lock:
            self.check_scope(client_id, role, scope)
            if role == "controller":
                self.controller = client_id
                self._change(controllerConnected=True)

    def applied(self, client_id, session, attach):
        with self.lock:
            if (attach["role"] == "editor" and self.candidate and
                    self.candidate["scope"] == session["scope"] and
                    attach["appliedRevision"] == session["revision"]):
                self._owner(client_id)
                target, self.candidate = self.candidate, None
                self._change(status="ready", target=target, error=None)

    def disconnected(self, client_id):
        with self.lock:
            if self.state["enabled"] and client_id == self.state["ownerClientId"]:
                self.owner_online = False
                self._unavailable("TEST_TARGET_UNAVAILABLE", "Test editor disconnected")
            if client_id == self.controller:
                self.controller = None
                self._change(controllerConnected=False)

    def detached(self, client_id, role):
        if role == "editor":
            self.disconnected(client_id)
        elif client_id == self.controller:
            self._change(controllerConnected=False)

    def _unavailable(self, code, message):
        target = self.state["target"] or self.candidate or self.resumable
        self._clear_target()
        self.resumable = copy.deepcopy(target)
        self._change(status="unavailable", target=None, controllerConnected=False,
                     error={"code": code, "message": message, "retryable": False})

    def expire(self):
        with self.lock:
            if self.state["enabled"] and self.owner_online and self.targets.clock() - self.last_seen > self.targets.timeout:
                self.owner_online = False
                self._unavailable("TEST_TARGET_UNAVAILABLE", "Test editor heartbeat expired")
