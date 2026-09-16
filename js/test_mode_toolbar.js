import {t} from "./i18n.js";
import {stable} from "./parameter_sync_client.js";

const iconUrl=new URL("./assets/vplugins.svg",import.meta.url).href;

export function testModeConnection(mode) {
  if(mode.exitPending)return "disconnected";
  if(!mode.enabled)return "off";
  const state=mode.state,session=mode.client?.session;
  return mode.online&&mode.owned&&state?.status==="ready"&&state.controllerConnected&&
    session&&stable(session.scope)===stable(state.target?.scope)?"connected":"disconnected";
}

/** The host owns placement/lifecycle; only our declared action button is decorated. */
export function testModeLabel(mode) {
  if(mode.exitPending)return t("PS Vplugins · Exited locally; server confirmation pending");
  if(!mode.enabled)return t("PS Vplugins · Test off");
  const name=mode.state?.target?.name||mode.tab().name;
  if(!mode.online)return t("PS Vplugins · Offline · {name}",{name});
  if(!mode.owned)return t("PS Vplugins · Another browser is testing");
  if(mode.state?.status==="unavailable")return t("PS Vplugins · Test unavailable · {name}",{name});
  if(mode.state?.status!=="ready")return t("PS Vplugins · Switching · {name}",{name});
  return mode.state.controllerConnected?t("PS Vplugins · Test on · {name}",{name})
    :t("PS Vplugins · Waiting for Vplugins · {name}",{name});
}

export function installTestModeToolbar(mode) {
  const style=document.createElement("style");
  style.textContent=`
    .ps-vplugins-test-toggle {--ps-icon-color:#969696;--ps-thumb-x:10px;
      display:inline-flex!important;align-items:center;gap:9px!important;
      height:30px!important;min-width:88px!important;padding:3px 8px!important;
      border:1px solid transparent!important;border-radius:6px!important;}
    .ps-vplugins-test-toggle:hover {background:var(--comfy-input-bg,#353535)!important;}
    .ps-vplugins-test-toggle:focus-visible {outline:2px solid var(--p-primary-color,#69b7ff);outline-offset:2px;}
    .ps-vplugins-test-toggle[data-test-connection="disconnected"] {--ps-icon-color:#ef5350;}
    .ps-vplugins-test-toggle[data-test-connection="connected"] {--ps-icon-color:#43c975;}
    .ps-vplugins-test-toggle[data-test-enabled="true"] {--ps-thumb-x:38px;}
    .ps-vplugins-test-toggle > * {display:none!important;}
    .ps-vplugins-test-toggle::before {content:"";display:block;flex:none;width:22px;height:22px;
      background:var(--ps-icon-color);mask:url("${iconUrl}") center/contain no-repeat;
      -webkit-mask:url("${iconUrl}") center/contain no-repeat;}
    .ps-vplugins-test-toggle::after {content:attr(data-test-toggle);display:block;flex:none;
      width:48px;height:22px;box-sizing:border-box;border-radius:11px;
      border:1px solid #747474;color:#f1f1f1;font:600 10px/20px system-ui,sans-serif;
      padding-left:16px;text-align:center;letter-spacing:.15px;
      background:radial-gradient(circle at var(--ps-thumb-x) 50%,#ededed 0 7px,transparent 7.5px) #484848;}
    .ps-vplugins-test-toggle[data-test-enabled="true"]::after {padding-left:0;padding-right:16px;}
  `;
  document.head.append(style);
  const render=()=>{
    const label=testModeLabel(mode);
    for(const button of document.querySelectorAll(".ps-vplugins-test-toggle")) {
      button.setAttribute("data-test-label",label);
      button.setAttribute("data-test-toggle",mode.enabled?"ON":"OFF");
      button.setAttribute("data-test-enabled",String(mode.enabled));
      button.setAttribute("data-test-connection",testModeConnection(mode));
      button.setAttribute("data-test-status",mode.online?mode.state?.status||"off":"unavailable");
      button.setAttribute("role","switch");
      button.setAttribute("aria-checked",String(mode.enabled));
      button.setAttribute("aria-label",label);
      button.setAttribute("title",[label,mode.error||mode.state?.error?.message,
        mode.state?.target?`workflowId: ${mode.state.target.scope.workflowId}\ngraphId: ${mode.state.target.scope.graphId}\ndraftRevision: ${mode.state.target.scope.draftRevision}`:null,
        t("Testing follows the current workflow tab. Click to enable or disable")].filter(Boolean).join("\n"));
    }
  };
  // Action bar can mount late or remount when host layout changes. Attribute writes
  // are deliberately excluded from observation to avoid a render feedback loop.
  const observer=new MutationObserver(render);
  observer.observe(document.body,{childList:true,subtree:true});
  render();
  return render;
}
