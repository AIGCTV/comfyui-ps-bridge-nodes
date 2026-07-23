import sys
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ps_bridge import backend_runner
from ps_bridge.manager import BridgeClient, BridgeManager


class BackendRunnerPatchTests(unittest.TestCase):
    def _workflow(self):
        return {
            "1": {
                "class_type": "Adv_Request",
                "inputs": {
                    "image_count": 1,
                    "prompt": "",
                    "resolution": "1k",
                    "strength": 0.65,
                    "batch_count": 1,
                    "seed": 42,
                    "send_to_ps": True,
                },
            },
            "2": {
                "class_type": "PSBridgeInt",
                "inputs": {
                    "slot_id": "steps",
                    "fallback": 20,
                    "min_value": 1,
                    "max_value": 100,
                    "step": 1,
                },
            },
            "3": {
                "class_type": "VpluginsRequest",
                "inputs": {
                    "main_image": "old.png",
                    "mask_image": "old-mask.png",
                    "prompt": "",
                    "params_json": "{}",
                },
            },
            "4": {
                "class_type": "Adv_SendToPS",
                "inputs": {},
            },
        }

    def _state(self):
        return {
            "request_id": "task-1",
            "feature_id": "roundtrip",
            "slots": {"int": {"steps": 35}},
            "adv_request": {
                "image_count": 2,
                "prompt": "a cat",
                "resolution": "1k",
                "strength": 0.55,
                "batch_count": 2,
                "seed": 123,
                "params_json": "{\"strength\":0.55}",
            },
            "vplugins_request": {
                "main_image": "",
                "mask_image": "",
                "prompt": "a cat",
                "params_json": "{\"strength\":0.55}",
            },
        }

    def test_patched_api_prompt_injects_request_slots_and_send_id(self):
        prompt = backend_runner.patched_api_prompt_for_state(self._workflow(), self._state())

        adv_inputs = prompt["1"]["inputs"]
        self.assertEqual(adv_inputs["image_count"], 2)
        self.assertEqual(adv_inputs["prompt"], "a cat")
        self.assertEqual(adv_inputs["strength"], 0.55)
        self.assertEqual(adv_inputs["batch_count"], 2)
        self.assertEqual(adv_inputs["seed"], 123)
        self.assertEqual(adv_inputs["params_json"], "{\"strength\":0.55}")
        self.assertNotIn("send_to_ps", adv_inputs)
        self.assertEqual(prompt["2"]["inputs"]["fallback"], 35)
        self.assertEqual(prompt["3"]["inputs"]["prompt"], "a cat")
        self.assertEqual(prompt["4"]["inputs"]["request_id"], "task-1")

    def test_patched_api_prompt_rejects_graph_workflow(self):
        with self.assertRaises(backend_runner.BackendRunnerError):
            backend_runner.patched_api_prompt_for_state({"nodes": [{"type": "Adv_Request"}]}, self._state())

    def test_patched_api_prompt_requires_send_node(self):
        workflow = self._workflow()
        workflow.pop("4")
        with self.assertRaises(backend_runner.BackendRunnerError):
            backend_runner.patched_api_prompt_for_state(workflow, self._state())


class DummyWebSocket:
    closed = False

    async def send_str(self, _payload):
        return None


class ManagerRoutingTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, role, client_id, **kwargs):
        return BridgeClient(DummyWebSocket(), role, client_id, "127.0.0.1", **kwargs)

    async def test_auto_frontend_online_forwards_without_requiring_workflow_file(self):
        manager = BridgeManager()
        ps_client = self._client("ps", "ps", base_url="http://127.0.0.1:8188")
        comfy_client = self._client("comfy", "comfy")
        comfy_client.capabilities = {"current_graph_workflows": True}
        manager.clients = {"ps": ps_client, "comfy": comfy_client}
        state = {
            "request_id": "task-1",
            "feature_id": "roundtrip",
            "execution_mode": "auto",
            "slots": {},
            "images": {},
            "selection": None,
        }

        with patch("ps_bridge.storage.ingest_run_payload", return_value=state) as ingest:
            with patch.object(manager, "send_to", new=AsyncMock()) as send_to:
                await manager.handle_ps_message(ps_client, "run_workflow", {"feature_id": "roundtrip"})

        ingest.assert_called_once()
        self.assertFalse(ingest.call_args.kwargs["require_workflow"])
        send_to.assert_any_call("comfy", "run_workflow", ANY)

    async def test_frontend_test_mode_overrides_manifest_api_workflow(self):
        manager = BridgeManager()
        ps_client = self._client("ps", "ps", base_url="http://127.0.0.1:8188")
        comfy_client = self._client("comfy", "comfy")
        comfy_client.capabilities = {"current_graph_workflows": True, "current_graph_test_mode": True}
        manager.clients = {"ps": ps_client, "comfy": comfy_client}
        state = {
            "request_id": "task-1",
            "feature_id": "roundtrip",
            "execution_mode": "api_workflow",
            "slots": {},
            "images": {},
            "selection": None,
        }

        with patch("ps_bridge.storage.execution_mode_for_payload", return_value="api_workflow"):
            with patch("ps_bridge.storage.ingest_run_payload", return_value=state) as ingest:
                with patch.object(manager, "send_to", new=AsyncMock()) as send_to:
                    await manager.handle_ps_message(ps_client, "run_workflow", {"feature_id": "roundtrip"})

        ingest.assert_called_once()
        self.assertFalse(ingest.call_args.kwargs["require_workflow"])
        run_payloads = [
            call.args[2]
            for call in send_to.call_args_list
            if call.args[:2] == ("comfy", "run_workflow")
        ]
        self.assertEqual(run_payloads[0]["execution_mode"], "current_graph")

    async def test_frontend_test_mode_overrides_explicit_api_workflow(self):
        manager = BridgeManager()
        ps_client = self._client("ps", "ps", base_url="http://127.0.0.1:8188")
        comfy_client = self._client("comfy", "comfy")
        comfy_client.capabilities = {"current_graph_workflows": True, "current_graph_test_mode": True}
        manager.clients = {"ps": ps_client, "comfy": comfy_client}
        state = {
            "request_id": "task-1",
            "feature_id": "roundtrip",
            "execution_mode": "api_workflow",
            "slots": {},
            "images": {},
            "selection": None,
        }

        with patch("ps_bridge.storage.ingest_run_payload", return_value=state):
            with patch("ps_bridge.backend_runner.queue_api_workflow_for_state", new=AsyncMock()) as queue:
                with patch.object(manager, "send_to", new=AsyncMock()) as send_to:
                    await manager.handle_ps_message(
                        ps_client,
                        "run_workflow",
                        {"feature_id": "roundtrip", "execution_mode": "api_workflow"},
                    )

        queue.assert_not_awaited()
        run_payloads = [
            call.args[2]
            for call in send_to.call_args_list
            if call.args[:2] == ("comfy", "run_workflow")
        ]
        self.assertEqual(run_payloads[0]["execution_mode"], "current_graph")

    async def test_multiple_test_mode_clients_prefers_focused_visible_latest_active(self):
        manager = BridgeManager()
        old_client = self._client("comfy", "old")
        old_client.capabilities = {
            "current_graph_workflows": True,
            "current_graph_test_mode": True,
            "graph_id": "old-graph",
            "page_visible": True,
            "window_focused": False,
            "last_active_at": 3000,
        }
        hidden_client = self._client("comfy", "hidden")
        hidden_client.capabilities = {
            "current_graph_workflows": True,
            "current_graph_test_mode": True,
            "graph_id": "hidden-graph",
            "page_visible": False,
            "window_focused": True,
            "last_active_at": 5000,
        }
        active_client = self._client("comfy", "active")
        active_client.capabilities = {
            "current_graph_workflows": True,
            "current_graph_test_mode": True,
            "graph_id": "active-graph",
            "page_visible": True,
            "window_focused": True,
            "last_active_at": 4000,
        }
        manager.clients = {"old": old_client, "hidden": hidden_client, "active": active_client}

        self.assertEqual(manager.primary_current_graph_test_client(), "active")

    async def test_current_graph_without_frontend_reports_clear_error(self):
        manager = BridgeManager()
        ps_client = self._client("ps", "ps", base_url="http://127.0.0.1:8188")
        manager.clients = {"ps": ps_client}

        with patch.object(manager, "send_to", new=AsyncMock()) as send_to:
            await manager.handle_ps_message(ps_client, "run_workflow", {"feature_id": "roundtrip", "execution_mode": "current_graph"})

        args = send_to.call_args.args
        self.assertEqual(args[1], "error")
        self.assertIn("current graph workflow support", args[2]["message"])

    async def test_api_workflow_routes_to_backend_runner(self):
        manager = BridgeManager()
        ps_client = self._client("ps", "ps", base_url="http://127.0.0.1:8188")
        manager.clients = {"ps": ps_client}
        state = {
            "request_id": "task-1",
            "feature_id": "roundtrip",
            "execution_mode": "api_workflow",
            "slots": {},
            "images": {},
            "selection": None,
        }
        queued = {"request_id": "task-1", "status": "queued_backend"}

        with patch("ps_bridge.storage.ingest_run_payload", return_value=state) as ingest:
            with patch("ps_bridge.backend_runner.queue_api_workflow_for_state", new=AsyncMock(return_value=queued)) as queue:
                with patch.object(manager, "send_to", new=AsyncMock()) as send_to:
                    await manager.handle_ps_message(ps_client, "run_workflow", {"feature_id": "roundtrip", "execution_mode": "api_workflow"})

        ingest.assert_called_once()
        self.assertNotIn("require_workflow", ingest.call_args.kwargs)
        queue.assert_awaited_once()
        send_to.assert_any_call("ps", "run_status", queued)


if __name__ == "__main__":
    unittest.main()
