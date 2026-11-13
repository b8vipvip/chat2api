from __future__ import annotations

from fastapi.responses import HTMLResponse


ADMIN_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>chat2api Console</title>
<style>
:root{color-scheme:dark;--bg:#09101d;--panel:#111a2b;--panel2:#0c1526;--line:#26344d;--text:#eef4ff;--muted:#8c9ab2;--accent:#72a7ff;--ok:#39d6a1;--warn:#ffd166;--bad:#ff727d}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}.shell{display:grid;grid-template-columns:220px 1fr;min-height:100vh}.side{border-right:1px solid var(--line);padding:20px 14px;background:#0b1322}.brand{font-size:22px;font-weight:850;padding:0 10px 18px}.brand small{display:block;color:var(--muted);font-size:11px}.nav button{display:block;width:100%;border:0;background:transparent;color:var(--muted);text-align:left;padding:10px 12px;border-radius:9px;cursor:pointer;margin:2px 0}.nav button.active,.nav button:hover{background:#17233a;color:var(--text)}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:15px 22px;border-bottom:1px solid var(--line);position:sticky;top:0;background:rgba(9,16,29,.94);z-index:5}.auth{display:flex;gap:8px;min-width:min(100%,560px)}input,select,button,textarea{font:inherit}.field,select,input,textarea,button.action{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:9px;padding:9px 11px}.auth input{flex:1}.action{cursor:pointer;background:#18345e!important}.action.good{background:#154634!important}.action.warn{background:#4b3d15!important}.action.danger{background:#4d2028!important}.main{min-width:0}.content{padding:22px;max-width:1600px;margin:auto}.view{display:none}.view.active{display:block}.muted{color:var(--muted)}.ok{color:var(--ok)}.warnText{color:var(--warn)}.bad{color:var(--bad)}.grid{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:12px;margin-bottom:14px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:15px}.card .n{font-size:25px;font-weight:850}.panels{display:grid;grid-template-columns:1fr 1fr;gap:14px}.wide{grid-column:1/-1}.scroll{overflow:auto;max-height:590px}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;border-bottom:1px solid var(--line);padding:8px 9px;vertical-align:top}th{color:var(--muted);font-weight:650;position:sticky;top:0;background:var(--panel)}code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}code{color:#c8d9ff}.pill{display:inline-block;padding:2px 7px;border-radius:999px;background:#1a2943}.pill.ok{background:#123b30}.pill.bad{background:#48242c}.pill.warn{background:#493b17}.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}.toolbar>*{min-width:120px}.secret{border:1px solid var(--ok);background:#07101e;padding:12px;border-radius:10px;word-break:break-all}.rowactions{display:flex;gap:6px}.rowactions button{padding:5px 7px}.split{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(330px,.75fr);gap:14px}.detail pre{white-space:pre-wrap;word-break:break-word;font-size:12px}.codebox{background:#07101e;border:1px solid var(--line);border-radius:10px;padding:12px;white-space:pre-wrap;overflow:auto}.doc h2{margin-top:26px}.doc p,.doc li{color:#cbd5e7}.labConfig{display:grid;grid-template-columns:180px 200px minmax(330px,1.2fr) minmax(260px,1fr) auto;gap:9px;align-items:end}.labConfig label{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}.testKeyBox{display:grid;grid-template-columns:1fr 1.25fr;gap:7px}.testKeyHint{grid-column:1/-1;font-size:11px;color:var(--muted);margin-top:-2px}.testStatus{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}.timeline{min-height:70px}.timelineRow{display:grid;grid-template-columns:130px 130px 1fr 100px 100px 100px;gap:8px;padding:8px;border-bottom:1px solid var(--line);align-items:center}.clickable{cursor:pointer}.clickable:hover{background:#172137}.hidden{display:none!important}.footer{margin-top:18px;color:var(--muted)}dialog{width:min(980px,92vw);max-height:86vh;border:1px solid var(--line);border-radius:14px;background:var(--panel);color:var(--text);padding:0;box-shadow:0 28px 90px rgba(0,0,0,.55)}dialog::backdrop{background:rgba(0,0,0,.66)}.dialogHead,.dialogFoot{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--line)}.dialogFoot{border-top:1px solid var(--line);border-bottom:0;justify-content:flex-end}.dialogBody{padding:16px;overflow:auto;max-height:65vh}.dialogBody pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px}.keyBadge{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.modeCard{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:16px;margin:12px 0}.modeHead{display:flex;justify-content:space-between;align-items:center;gap:18px}.modeHead h3{margin:0 0 5px}.modeHead p{margin:0}.modeMeta{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.modeGate{margin-top:14px;padding:12px;border:1px solid #755c27;border-radius:10px;background:#2a2415}.modeGate strong{color:var(--warn)}.modeSwitch{position:relative;display:inline-flex;width:54px;height:30px;flex:0 0 auto}.modeSwitch input{position:absolute;opacity:0;width:1px;height:1px}.modeSwitch .track{position:absolute;inset:0;border-radius:99px;background:#34445e;cursor:pointer;transition:background .18s}.modeSwitch .track:before{content:"";position:absolute;width:22px;height:22px;left:4px;top:4px;border-radius:50%;background:#eef4ff;transition:transform .18s}.modeSwitch input:checked+.track{background:#315d9d}.modeSwitch input:checked+.track:before{transform:translateX(24px)}.modeSwitch input:focus-visible+.track{outline:2px solid var(--accent);outline-offset:3px}.modeActions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:14px}.sessionAuth{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.sessionAuth input{width:130px;min-width:110px}.sessionAuth button{white-space:nowrap}.c-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.c-form{display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin:12px 0}.c-form label{display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:12px}.c-form input,.c-form select,.c-form textarea{min-width:150px}.c-form textarea{min-width:260px;min-height:76px}.c-note{border:1px solid var(--line);border-radius:10px;padding:10px;color:var(--muted);margin:10px 0}.c-table{width:100%;border-collapse:collapse}.c-table td,.c-table th{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}.c-table th{color:var(--muted)}@media(max-width:1050px){.sessionAuth{min-width:0}.sessionAuth input{width:100px}.c-grid{grid-template-columns:1fr}.c-form input,.c-form select{width:100%}}
@media(max-width:1250px){.labConfig{grid-template-columns:1fr 1fr}.labConfig>button{align-self:end}.testKeyBox{grid-template-columns:1fr}}
@media(max-width:1050px){.shell{grid-template-columns:1fr}.side{border-right:0;border-bottom:1px solid var(--line)}.nav{display:flex;overflow:auto}.nav button{width:auto;white-space:nowrap}.top{position:static}.grid{grid-template-columns:repeat(2,1fr)}.panels,.split{grid-template-columns:1fr}.wide{grid-column:auto}.labConfig{grid-template-columns:1fr}.timelineRow{grid-template-columns:1fr 1fr}.auth{min-width:0}.testKeyBox{grid-template-columns:1fr}}
</style></head><body><div class="shell">
<aside class="side"><div class="brand">chat2api<small>Server Console · v0.5</small></div><div class="nav"><button data-view="overview">概览</button><button data-view="modes">模式管理</button><button data-view="keys">API Key</button><button data-view="requests">请求记录</button><button data-view="media-storage">转存管理</button><button data-view="docs">开发文档</button><button data-view="playground">测试场</button><button data-view="control-admins" data-cview>管理员 RBAC</button><button data-view="client-policy" data-cview>客户端配置</button><button data-view="registration-invitations" data-cview>邀请注册</button><button data-view="tenants" data-cview>租户管理</button><button data-view="model-entitlements" data-cview>模型权限</button><button data-view="plugins" data-cview>插件目录</button><button data-view="quota-ledger" data-cview>额度预留</button><button data-view="api-keys" data-cview>API Key 管理</button></div></aside>
<main class="main"><header class="top"><div><b id="pageTitle">概览</b><div id="status" class="muted">输入管理员主密钥后连接</div></div><div class="auth"><input id="adminKey" type="password" placeholder="CHAT2API_API_KEY 管理员主密钥"><button class="action" id="connect">连接</button><button class="action" id="refresh">刷新</button></div><div class="sessionAuth"><input id="adminSessionUser" autocomplete="username" placeholder="管理员用户名"><input id="adminSessionPassword" type="password" autocomplete="current-password" placeholder="管理员密码"><button class="action good" id="adminSessionLogin">会话登录</button><button class="action" id="adminSessionLogout">退出会话</button><button class="action" id="adminSessionRotatePassword" hidden>修改我的密码</button><span id="adminSessionState" class="muted">未检查管理会话</span></div></header><div class="content">
<section class="view" id="view-overview"><div class="grid"><div class="card"><div class="muted">在线扩展</div><div class="n" id="sExt">-</div></div><div class="card"><div class="muted">请求记录</div><div class="n" id="sReq">-</div></div><div class="card"><div class="muted">成功率</div><div class="n" id="sSuccess">-</div></div><div class="card"><div class="muted">平均首 Token</div><div class="n" id="sFirst">-</div></div><div class="card"><div class="muted">估算 Token</div><div class="n" id="sToken">-</div></div></div><div class="panels"><div class="panel"><h3>Chrome 扩展</h3><table><thead><tr><th>ID</th><th>名称</th><th>状态</th><th>版本</th><th>能力</th></tr></thead><tbody id="extBody"></tbody></table></div><div class="panel"><h3>模型目录</h3><table><thead><tr><th>ID</th><th>能力</th></tr></thead><tbody id="modelBody"></tbody></table></div><div class="panel wide"><h3>最近请求</h3><div class="scroll"><table><thead><tr><th>时间</th><th>类型</th><th>状态</th><th>Key</th><th>模型</th><th>首包</th><th>总耗时</th><th>Token</th></tr></thead><tbody id="recentBody"></tbody></table></div></div></div></section>
<section class="view" id="view-modes">
  <div class="panel">
    <h2>模式管理</h2>
    <p class="muted">管理服务端模式配置。当前未指定模式的 API 请求固定使用 Mode 2；本页面的 Mode 1 开关只记录可行性测试意向，不改变请求路由。</p>
    <div class="modeCard">
      <div class="modeHead">
        <div>
          <div class="modeMeta"><h3>Mode 2 · OpenAI 兼容 API</h3><span class="pill ok">默认启用</span></div>
          <p class="muted">现有 /v1/* API、SDK、Worker 调度和请求处理保持当前行为。未明确指定模式时继续使用 Mode 2。</p>
        </div>
      </div>
    </div>
    <div class="modeCard">
      <div class="modeHead">
        <div>
          <h3>Mode 1 · Cookie 代理</h3>
          <p class="muted">仅用于后台配置与可行性测试，不代表代理服务已获准或已实现。</p>
        </div>
        <label class="modeSwitch" title="保存 Mode 1 测试配置意向">
          <input id="mode1CookieProxyRequested" type="checkbox" aria-label="Mode 1 Cookie 代理测试开关">
          <span class="track"></span>
        </label>
      </div>
      <div class="modeGate">
        <strong>NO-GO 授权门禁 · 实际运行能力始终关闭</strong>
        <p>托管多用户、服务端持有 ChatGPT session/Cookie 并代理私有网页 API，当前不得进入生产实现。须先取得 OpenAI 明确书面授权 / partner approval，并完成独立安全审查。</p>
      </div>
      <p id="mode1RuntimeStatus" class="warnText">实际运行：关闭。打开上方开关只保存测试意向，不会启动代理。</p>
      <p class="muted">本开关不会收集、读取、保存或转发 ChatGPT Cookie / session，不会创建私有网页代理路由，不会绕过登录、访问控制、验证码或其他保护措施。即使测试配置为开启，服务端 effective_enabled 仍固定为 false。</p>
      <div class="modeActions">
        <button class="action good" id="saveModeSettings">保存测试开关</button>
        <button class="action" id="reloadModeSettings">重新读取</button>
        <span id="modeSettingsStatus" class="muted">尚未读取</span>
      </div>
    </div>
  </div>
</section>
<section class="view" id="view-keys"><div class="panel"><h3>创建业务 API Key</h3><div class="toolbar"><input id="keyName" placeholder="名称"><input id="keyDays" type="number" min="1" max="3650" placeholder="有效天数"><input id="keyQuota" type="number" min="0" placeholder="Token 额度（留空不限）"><button class="action good" id="createKey">创建</button></div><div class="secret hidden" id="newSecret"><span id="newToken"></span> <button class="action" id="copyNew">复制</button></div></div><div class="panel" style="margin-top:14px"><h3>令牌列表</h3><div class="scroll"><table><thead><tr><th>名称</th><th>前缀</th><th>状态</th><th>权限</th><th>最后使用</th><th>请求/Token</th><th>Token 额度</th><th>操作</th></tr></thead><tbody id="keysBody"></tbody></table></div></div><div class="footer">新版本业务 Key 的可复制副本使用管理员主密钥派生密钥加密保存；鉴权仍只比较哈希。旧版 0.4 Key 没有密文副本，因此无法恢复。</div></section>
<section class="view" id="view-requests"><div class="split"><div class="panel"><h3>请求记录</h3><div class="toolbar"><input id="rqSearch" placeholder="request / error / client"><select id="rqStatus"><option value="">全部状态</option><option>completed</option><option>error</option></select><input id="rqModel" placeholder="模型"><button class="action" id="rqGo">筛选</button></div><div class="scroll"><table><thead><tr><th>时间</th><th>类型</th><th>状态</th><th>Key</th><th>模型</th><th>附件</th><th>首包</th><th>总耗时</th><th>Token</th></tr></thead><tbody id="rqBody"></tbody></table></div></div><div class="panel detail"><h3>请求详情</h3><div id="rqDetail" class="muted">点击记录查看诊断。</div></div></div></section>
<section class="view" id="view-media-storage">
  <div class="panel">
    <h2>转存管理</h2>
    <p class="muted">生成的图片和可下载文件共用默认本地/S3 存储配置；图片额外生成缩略图，文件作为安全下载附件交付。仅支持已验证并实际传输成功的生成文件。</p>
    <div class="toolbar">
      <label><input id="mediaAutoArchive" type="checkbox" checked> 自动转存生成的图片与文件（关闭后不交付未归档的附件）</label>
      <label>默认存储位置 <select id="mediaBackend"><option value="local">服务器本地磁盘</option><option value="s3">S3 兼容对象存储</option></select></label>
    </div>
    <div class="toolbar"><label style="flex:1">公开访问域名（HTTPS，留空使用 API 当前域名）<input id="mediaPublicBase" class="field" style="width:100%" placeholder="https://chat2api.mv3.cn"></label></div>
    <fieldset style="border:1px solid var(--line);border-radius:10px;padding:12px"><legend>S3 / Cloudflare R2 / MinIO 兼容配置</legend>
      <div class="toolbar">
        <input id="mediaS3Bucket" placeholder="Bucket 名称" autocomplete="off">
        <input id="mediaS3Region" placeholder="区域，如 us-east-1" autocomplete="off">
        <input id="mediaS3Endpoint" placeholder="HTTPS Endpoint，可留空使用 AWS S3" style="min-width:300px" autocomplete="off">
      </div>
      <div class="toolbar">
        <input id="mediaS3Key" placeholder="Access Key ID（留空保留）" autocomplete="off">
        <input id="mediaS3Secret" type="password" placeholder="Secret Key（留空保留）" autocomplete="new-password">
      </div>
      <p class="muted">凭据在服务端加密保存，不会回传至浏览器。S3 目标必须具备写入、读取权限。云数据库保存媒体元数据或其它非 S3 平台尚未提供可用驱动，暂不显示为已支持。</p>
    </fieldset>
    <div class="toolbar" style="margin-top:14px"><button class="action good" id="mediaSaveSettings">保存转存策略</button><button class="action" id="mediaReloadSettings">重新读取</button><button class="action" id="mediaTestSettings">测试存储连接</button><button class="action" id="mediaBackfillThumbnails" title="每轮最多扫描 250 项；不修改原图或分享链接">补全历史缩略图</button><span class="muted" id="mediaStorageStatus">尚未连接</span></div>
  </div>
  <div class="panel" style="margin-top:14px"><h3>已转存媒体</h3><p id="mediaSummary" class="muted"></p>
    <div class="scroll"><table><thead><tr><th>时间</th><th>请求 ID</th><th>媒体类型</th><th>尺寸</th><th>大小</th><th>位置</th><th>操作</th></tr></thead><tbody id="mediaStoredRows"></tbody></table></div></div>
  <div class="panel" style="margin-top:14px"><h3>已转存生成文件</h3><p id="generatedFileSummary" class="muted">从 Worker 识别并归档的 PDF、Office、ZIP、代码和其它允许的文件。</p>
    <div class="scroll"><table><thead><tr><th>时间</th><th>请求 ID</th><th>文件名</th><th>MIME</th><th>大小</th><th>位置</th><th>操作</th></tr></thead><tbody id="generatedFileRows"></tbody></table></div></div>
</section>
<section class="view" id="view-control-admins">
  <div class="panel"><h2>管理员 RBAC</h2><p class="c-note">管理员密码仅在创建时提交；服务端仅保存 PBKDF2 哈希。禁用账户会撤销其现有 Session。权限最终由服务端 RBAC 决定，隐藏按钮不是安全边界。</p>
    <div class="c-form"><label>用户名<input id="cAdminName" autocomplete="off" placeholder="support_agent"></label><label>初始密码<input id="cAdminPassword" type="password" autocomplete="new-password" placeholder="至少 12 位"></label><label>角色<select id="cAdminRole"></select></label><button class="action good" id="cAdminCreate">创建管理员</button><button class="action" id="cAdminReload">刷新</button></div>
    <div id="cAdminStatus" class="muted"></div><div class="scroll"><table class="c-table"><thead><tr><th>用户名</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody id="cAdminRows"></tbody></table></div>
  </div>
</section>
<section class="view" id="view-client-policy">
  <div class="panel"><h2>客户端动态配置</h2><p class="c-note">注册开关和 Web Chat 开关由服务端强制执行；公告作为纯文本返回，客户端必须使用 textContent 渲染。</p>
    <div class="c-form"><label>注册策略<select id="cRegistrationMode"><option value="open">开放注册</option><option value="invite_only">邀请注册</option><option value="closed">关闭注册</option></select></label><label><span>Web Chat</span><select id="cWebChatEnabled"><option value="true">启用</option><option value="false">停用</option></select></label></div>
    <label>公告（最多 500 字）<textarea id="cAnnouncement" maxlength="500" style="width:100%;min-height:110px"></textarea></label>
    <div class="c-form"><button class="action good" id="cClientSave">保存配置</button><button class="action" id="cClientReload">重新读取</button><span id="cClientStatus" class="muted"></span></div>
  </div>
</section>
<section class="view" id="view-registration-invitations">
  <div class="panel"><h2>邀请注册</h2><p class="c-note">仅在注册策略为 invite_only 时可签发邀请。邀请绑定邮箱、单次使用并有过期时间；原始代码只在创建响应中显示一次，列表和审计不会返回代码。</p>
    <div class="c-form"><label>受邀邮箱<input id="cInviteEmail" type="email" autocomplete="off" placeholder="user@example.com"></label><label>有效期<select id="cInviteTtl"><option value="86400">24 小时</option><option value="604800" selected>7 天</option><option value="2592000">30 天</option></select></label><button class="action good" id="cInviteCreate">生成邀请代码</button><button class="action" id="cInviteReload">刷新列表</button></div>
    <div id="cInviteStatus" class="muted"></div>
    <div id="cInviteCodePanel" class="c-note hidden"><strong>新邀请代码（仅显示本次）：</strong><code id="cInviteCode"></code><button class="action" id="cInviteCopy">复制代码</button></div>
    <div class="scroll"><table class="c-table"><thead><tr><th>邮箱</th><th>状态</th><th>创建者</th><th>创建时间</th><th>过期时间</th><th>操作</th></tr></thead><tbody id="cInviteRows"></tbody></table></div>
  </div>
</section>
<section class="view" id="view-tenants">
  <div class="c-grid">
    <div class="panel"><h2>工作区 / 租户</h2><div class="c-form"><label>Slug<input id="cTenantSlug" placeholder="acme-team"></label><label>显示名称<input id="cTenantName" placeholder="Acme Team"></label><button class="action good" id="cTenantCreate">创建工作区</button><button class="action" id="cTenantReload">刷新</button></div><div class="scroll"><table class="c-table"><thead><tr><th>名称</th><th>Tenant ID</th><th>操作</th></tr></thead><tbody id="cTenantRows"></tbody></table></div></div>
    <div class="panel"><h2>成员关系</h2><div id="cTenantSelected" class="muted">请先选择工作区</div><div class="c-form"><label>User ID<input id="cTenantUserId" placeholder="usr_..."></label><label>角色<select id="cTenantMemberRole"><option>owner</option><option>admin</option><option>member</option><option>viewer</option></select></label><button class="action good" id="cTenantAddMember">添加 / 更新成员</button></div><div class="scroll"><table class="c-table"><thead><tr><th>User ID</th><th>角色</th><th>操作</th></tr></thead><tbody id="cTenantMemberRows"></tbody></table></div></div>
    <div class="panel wide"><h2>API Key 工作区绑定</h2><p class="c-note">仅允许绑定该用户真实拥有且已存在的 API Key。一个 Key 只能绑定一个工作区；移除成员前必须先解绑其 Key。</p><div class="c-form"><label>User ID<input id="cTenantKeyUser" placeholder="usr_..."></label><label>API Key ID<input id="cTenantKeyId" placeholder="key_..."></label><button class="action good" id="cTenantBindKey">绑定 Key</button><button class="action" id="cTenantReloadKeys">刷新绑定</button></div><div class="scroll"><table class="c-table"><thead><tr><th>API Key ID</th><th>User ID</th><th>创建时间</th><th>操作</th></tr></thead><tbody id="cTenantKeyRows"></tbody></table></div></div>
  </div>
</section>
<section class="view" id="view-model-entitlements">
  <div class="panel"><h2>模型权限策略</h2><p class="c-note">Allowlist 在 global / tenant / user / API Key 之间取交集；任意 Deny 优先。默认别名 default、chatgpt-web、auto 在存在策略时会拒绝放行。这里只管理权限，不决定 Worker 实际可用模型，也不伪造 served-model 证据。</p>
    <div class="c-form"><label>主体类型<select id="cEntSubjectType"><option value="global">全局</option><option value="tenant">租户</option><option value="tenant_role">租户角色</option><option value="user">用户</option><option value="key">API Key</option></select></label><label>主体 ID<input id="cEntSubjectId" placeholder="global 使用 default"></label><label>策略效果<select id="cEntEffect"><option value="allow">Allowlist</option><option value="deny">Denylist</option></select></label><label>模型 ID（逗号 / 换行分隔）<textarea id="cEntModels" placeholder="gpt-5.5&#10;gpt-5.6-sol"></textarea></label><button class="action good" id="cEntSave">保存策略</button><button class="action" id="cEntReload">刷新</button></div>
    <div id="cEntStatus" class="muted"></div><div class="scroll"><table class="c-table"><thead><tr><th>主体</th><th>效果</th><th>模型</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody id="cEntRows"></tbody></table></div>
  </div>
</section>
<section class="view" id="view-plugins">
  <div class="panel"><h2>产品插件目录</h2><p class="c-note">当前只允许登记经过字段校验的 Manifest 元数据。所有插件仍然 untrusted，执行能力固定关闭；SHA-256 仅是完整性指纹，不是签名或信任证明。</p>
    <label>Manifest JSON<textarea id="cPluginManifest" spellcheck="false" style="width:100%;min-height:260px;font-family:ui-monospace,monospace" placeholder='{"id":"sample_tool","name":"Sample Tool","description":"...","publisher":"...","version":"1.0.0","tools":[...]}'></textarea></label>
    <div class="c-form"><button class="action good" id="cPluginRegister">登记 Manifest</button><button class="action" id="cPluginReload">刷新目录</button><span id="cPluginStatus" class="muted"></span></div>
    <div class="scroll"><table class="c-table"><thead><tr><th>插件</th><th>版本</th><th>SHA-256</th><th>信任</th><th>执行</th><th>操作</th></tr></thead><tbody id="cPluginRows"></tbody></table></div>
    <pre id="cPluginDetail" class="codebox" style="margin-top:12px;white-space:pre-wrap"></pre>
  </div>
</section>
<section class="view" id="view-api-keys">
  <div class="panel"><h2>API Key 管理</h2><p class="c-note">API Key Admin 可创建、调整额度、停用或撤销 Key。完整 Token 只在创建时显示一次；本页不提供历史密钥读取接口。</p>
    <div class="c-form"><label>名称<input id="cKeyName" placeholder="integration-prod"></label><label>有效天数<input id="cKeyExpiry" type="number" min="1" max="3650" placeholder="留空不过期"></label><label>Token 额度<input id="cKeyQuota" type="number" min="0" placeholder="留空不限"></label><button class="action good" id="cKeyCreate">创建 Key</button><button class="action" id="cKeyReload">刷新</button></div>
    <div id="cKeySecret" class="secret hidden" style="margin:10px 0;white-space:pre-wrap;word-break:break-all"></div><div id="cKeyStatus" class="muted"></div>
    <div class="scroll"><table class="c-table"><thead><tr><th>Key ID</th><th>名称</th><th>状态</th><th>Token 使用</th><th>额度</th><th>操作</th></tr></thead><tbody id="cKeyRows"></tbody></table></div>
  </div>
</section>
<section class="view" id="view-quota-ledger">
  <div class="panel"><h2>API Key 配额预留流水</h2><p class="c-note">这里只显示尚未完成的 token 预留和与 ApiKeyStore 完成用量账本的恢复状态。它不是钱包余额、已完成 usage 或退款账本；当前运行时接入状态会由服务端明确显示。</p>
    <div class="c-form"><label>状态筛选<select id="cQuotaState"><option value="">全部</option><option value="reserved">reserved</option><option value="committing">committing</option><option value="settled">settled</option><option value="released">released</option><option value="expired">expired</option></select></label><label>API Key ID<input id="cQuotaKeyId" placeholder="可选"></label><button class="action" id="cQuotaReload">刷新</button><button class="action good" id="cQuotaReconcile">恢复未完成结算</button></div>
    <div id="cQuotaStatus" class="muted"></div><div class="scroll"><table class="c-table"><thead><tr><th>Request ID</th><th>API Key ID</th><th>预留 Token</th><th>实际 Token</th><th>状态</th><th>创建时间</th><th>过期时间</th></tr></thead><tbody id="cQuotaRows"></tbody></table></div>
  </div>
</section>
<section class="view doc" id="view-docs"><div class="panel"><h1>开发文档</h1><p>Base URL：<code id="baseUrl"></code>。业务 Key 支持文本、视觉/文件理解和图片生成；Chrome 必须打开且扩展在线，当前版本不再依赖 Windows 桌面客户端。</p><h2>文本 / 视觉 / 文件理解</h2><div class="codebox" id="docChat"></div><p>先通过 <code>POST /v1/files</code> 上传 base64 文件，再把返回的 <code>file_id</code> 放入 <code>attachments</code>。图片也支持 OpenAI 风格 <code>image_url</code> 的 base64 data URL。</p><h2>上传文件</h2><div class="codebox" id="docFile"></div><h2>图片生成</h2><div class="codebox" id="docImage"></div><p><code>gpt-image</code> 会路由到 <code>https://chatgpt.com/images/</code>。参考图使用同一个 <code>attachments</code> 字段。当前 <code>n</code> 固定为 1，推荐 <code>response_format=b64_json</code>。</p><h2>状态码</h2><ul><li>401：Key 无效/过期/停用。</li><li>409：扩展忙或多个扩展未指定 client_id。</li><li>503：Chrome 扩展离线。当前不会再尝试唤醒桌面客户端。</li><li>504：等待 ChatGPT/Images 超时。</li></ul><p><a href="/docs" target="_blank" style="color:var(--accent)">打开 FastAPI OpenAPI</a></p></div></section>
<section class="view" id="view-playground"><div class="panel"><h2>自动测试场</h2><div class="muted">执行标准化用例并生成质量报告。测试 Key 支持选择现有业务 Key 或手动粘贴；手动粘贴优先，未指定时默认使用管理员 CHAT2API_API_KEY。</div><div class="labConfig" style="margin-top:14px"><div><label>测试类型</label><select id="testType"><option value="text">文本</option><option value="vision">视觉理解</option><option value="file">文件理解</option><option value="image_generation">图片生成</option><option value="voice_generation">语音生成</option><option value="voice_call">语音通话</option><option value="all">全部测试</option></select></div><div><label>文本/理解模型</label><select id="testModel"><option value="default">默认（页面当前模型）</option></select></div><div><label>测试 API Key</label><div class="testKeyBox"><select id="testKeySelect"><option value="master">管理员 CHAT2API_API_KEY（默认）</option></select><input id="testKeyInput" type="password" autocomplete="off" placeholder="粘贴 API Key（可选，优先使用）"><div class="testKeyHint">留空时使用左侧选择项；默认选择管理员主 Key。完整 Key 不写入测试报告。</div></div></div><div><label>测试附件（可多选：图片 / PDF / 文档等）</label><input id="testFiles" type="file" multiple></div><button class="action good" id="startTest">开始测试</button></div><div class="testStatus"><div class="card"><div class="muted">当前状态</div><div class="n" id="testState">待命</div></div><div class="card"><div class="muted">通过</div><div class="n" id="testPass">0</div></div><div class="card"><div class="muted">警告</div><div class="n" id="testWarn">0</div></div><div class="card"><div class="muted">失败</div><div class="n" id="testFail">0</div></div></div><div class="panel"><h3>实时监测</h3><div class="timeline" id="timeline"><div class="muted">尚未开始测试。</div></div></div></div><div class="panel" style="margin-top:14px"><h3>测试记录</h3><div class="scroll"><table><thead><tr><th>时间</th><th>类型</th><th>状态</th><th>模型</th><th>测试 Key</th><th>耗时</th><th>摘要</th></tr></thead><tbody id="testHistory"></tbody></table></div></div></section>
<div class="footer">Token 为 chat2api 本地估算，不是 ChatGPT 官方 usage。语音能力在本版测试场中会标记为未实现/跳过，不伪装为通过。</div></div></main></div>

<script src="/assets/chat2api-control-plane-admin-v1.js" defer></script>
<script>
const INITIAL_VIEW='__INITIAL_VIEW__';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtMs=v=>v==null?'-':(Number(v)<1000?Math.round(v)+' ms':(Number(v)/1000).toFixed(1)+' s');
const fmtTime=v=>v?String(v).replace('T',' ').slice(0,19):'-';
let overview=null;
let testKeyCatalog=[];
let activeTestReport=null;
function key(){return sessionStorage.getItem('chat2api_admin_key')||''}
function headers(k=key()){return{Authorization:'Bearer '+k}}
async function api(path,opt={}){const h={...headers(opt.key||key()),...(opt.headers||{})};if(opt.body!==undefined)h['Content-Type']='application/json';const r=await fetch(path,{method:opt.method||'GET',headers:h,body:opt.body===undefined?undefined:JSON.stringify(opt.body),cache:'no-store',keepalive:opt.keepalive===true,signal:opt.signal});const t=await r.text();let d={};try{d=t?JSON.parse(t):{}}catch(_){d={detail:t}}if(!r.ok)throw new Error(d.detail||`${r.status} ${t}`);return d}
function pill(v){const c=v==='completed'||v==='passed'||v==='online'?'ok':v==='warning'||v==='skipped'||v==='busy'?'warn':'bad';return`<span class="pill ${c}">${esc(v)}</span>`}
function status(t,c='muted'){$('status').className=c;$('status').textContent=t}
$('adminKey').value=key();
$('connect').onclick=()=>{sessionStorage.setItem('chat2api_admin_key',$('adminKey').value.trim());loadOverview()};
$('refresh').onclick=()=>show((location.hash||'#overview').slice(1));
const titles={overview:'概览',modes:'模式管理',keys:'API Key',requests:'请求记录','media-storage':'转存管理',docs:'开发文档',playground:'测试场','control-admins':'管理员 RBAC','client-policy':'客户端配置','registration-invitations':'邀请注册',tenants:'租户管理','model-entitlements':'模型权限',plugins:'插件目录','quota-ledger':'额度预留','api-keys':'API Key 管理'};
function show(v){document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.view===v));$('view-'+v)?.classList.add('active');$('pageTitle').textContent=titles[v]||v;location.hash=v;if(v==='overview')loadOverview();if(v==='modes')loadModes();if(v==='keys')loadKeys();if(v==='requests')loadRequests();if(v==='media-storage')loadMediaStorage();if(v==='docs')renderDocs();if(v==='playground'){loadOverview().then(loadTests)}}
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>show(b.dataset.view));
let modeSettings=null;
function renderModeSettings(data){
  modeSettings=data;
  const mode1=data?.mode1_cookie_proxy||{};
  $('mode1CookieProxyRequested').checked=mode1.requested_enabled===true;
  $('mode1RuntimeStatus').textContent=mode1.effective_enabled===true
    ? '实际运行：已启用'
    : (mode1.requested_enabled===true
      ? '测试开关已开启并已保存；实际运行仍为关闭，NO-GO 授权门禁继续阻断。'
      : '测试开关：默认关闭。实际运行：关闭，NO-GO 授权门禁继续阻断。');
  $('modeSettingsStatus').textContent=mode1.updated_at
    ? '最近保存：'+fmtTime(mode1.updated_at)
    : '尚未保存过 Mode 1 测试意向';
}
async function loadModes(){
  if(!key()){status('请先登录管理员账号');return}
  try{
    const data=await api('/api/admin/modes');
    renderModeSettings(data);
    status('模式配置已读取；Mode 1 实际运行仍受 NO-GO 阻断','ok');
  }catch(error){
    $('modeSettingsStatus').textContent='读取失败：'+error.message;
    status('模式配置读取失败：'+error.message,'bad');
  }
}
$('mode1CookieProxyRequested')?.addEventListener('change',()=>{
  $('modeSettingsStatus').textContent='有未保存的测试配置变更';
});
$('saveModeSettings')?.addEventListener('click',async()=>{
  const button=$('saveModeSettings');
  button.disabled=true;
  try{
    const data=await api('/api/admin/modes',{
      method:'PUT',
      body:{mode1_cookie_proxy_requested:$('mode1CookieProxyRequested').checked}
    });
    renderModeSettings(data);
    $('modeSettingsStatus').textContent=data.mode1_cookie_proxy?.requested_enabled===true
      ? '已保存测试开启意向；实际代理仍被 NO-GO 阻断'
      : '已保存：Mode 1 测试开关关闭';
    status('模式配置已保存；未启用任何 Cookie 代理','ok');
  }catch(error){
    $('modeSettingsStatus').textContent='保存失败：'+error.message;
    status('模式配置保存失败：'+error.message,'bad');
  }finally{
    button.disabled=false;
  }
});
$('reloadModeSettings')?.addEventListener('click',loadModes);
async function loadOverview(){if(!key()){status('请输入管理员主密钥');return}try{overview=await api('/api/admin/overview');status(`已连接 · v${overview.version}`,'ok');const s=overview.telemetry||{};$('sExt').textContent=overview.health?.online_extensions??0;$('sReq').textContent=s.retained_requests??0;$('sSuccess').textContent=(s.success_rate??100)+'%';$('sFirst').textContent=fmtMs(s.avg_first_token_ms);$('sToken').textContent=s.estimated_total_tokens??0;$('extBody').innerHTML=(overview.clients||[]).map(c=>`<tr><td><code>${esc(c.client_id)}</code></td><td>${esc(c.name)}</td><td>${pill(c.online?(c.busy?'busy':'online'):'offline')}</td><td>${esc(c.version)}</td><td>${esc((c.metadata?.capabilities||[]).join(', '))}</td></tr>`).join('')||'<tr><td colspan="5">暂无扩展</td></tr>';$('modelBody').innerHTML=(overview.models||[]).map(m=>`<tr><td><code>${esc(m.id)}</code></td><td>${esc((m.capabilities||[]).join(', '))}</td></tr>`).join('');$('recentBody').innerHTML=(overview.recent_requests||[]).map(r=>`<tr><td>${fmtTime(r.recorded_at)}</td><td>${esc(r.request_type||'text')}</td><td>${pill(r.status)}</td><td>${esc(r.api_key_name||'-')}</td><td><code>${esc(r.requested_model||'-')}</code></td><td>${fmtMs(r.timings?.first_token_ms)}</td><td>${fmtMs(r.timings?.total_ms)}</td><td>${esc(r.usage?.total_tokens??0)}</td></tr>`).join('');fillModels();return overview}catch(e){status('连接失败：'+e.message,'bad')}}
// Test only concrete models discovered AND validated on an online Worker.
// Preserve the operator selection across live catalog refreshes.
function fillModels(rows=overview?.models||[]){
  if(globalThis.__CHAT2API_TEXT_MODEL_MANAGEMENT_V146__)return;
  const picker=$('testModel');if(!picker)return;
  const previous=picker.value;
  const choices=(Array.isArray(rows)?rows:[]).filter(row=>
    row&&row.validated===true&&Array.isArray(row.clients)&&row.clients.length>0&&
    (row.capabilities||[]).includes('text')&&
    /^gpt-[a-z0-9.]+(?:-[a-z0-9.]+)*$|^o[0-9]+(?:-[a-z0-9]+)*$/i.test(String(row.id||''))
  );
  const byId=new Map(choices.map(row=>[String(row.id),row]));
  picker.disabled=false;
  picker.innerHTML='<option value="default">默认（页面当前模型）</option>' +
    [...byId.values()].map(row=>
      `<option value="${esc(row.id)}">${esc(row.id)} · ${esc((row.clients||[]).length)} Worker</option>`
    ).join('');
  picker.value=previous === "default" || byId.has(previous) ? previous : "default";
}
let testModelRefreshBusy=false;
async function refreshTestModels(){
  if(globalThis.__CHAT2API_TEXT_MODEL_MANAGEMENT_V146__)return;
  if(!key()||testModelRefreshBusy)return;
  testModelRefreshBusy=true;
  try{const catalog=await api('/v1/models');fillModels(catalog.data||[])}
  catch(error){console.warn('Worker model catalog refresh failed',error)}
  finally{testModelRefreshBusy=false}
}
$('testModel')?.addEventListener('focus',()=>refreshTestModels());
setInterval(()=>{if(document.getElementById('view-playground')?.classList.contains('active'))refreshTestModels()},15000);
async function loadKeys(){if(!key())return;try{const d=await api('/api/admin/keys');$('keysBody').innerHTML=(d.data||[]).map(k=>{let actions=k.managed&&!k.revoked_at?`<div class="rowactions">${k.secret_recoverable?`<button class="action" onclick="copyKey('${esc(k.key_id)}')">复制 Key</button>`:''}<button class="action" onclick="toggleKey('${esc(k.key_id)}',${!k.enabled})">${k.enabled?'停用':'启用'}</button><button class="action danger" onclick="revokeKey('${esc(k.key_id)}')">撤销</button></div>`:'<span class="muted">.env 管理</span>';return`<tr><td>${esc(k.name)}</td><td><code>${esc(k.prefix)}</code></td><td>${pill(k.revoked_at?'revoked':k.expired?'expired':k.enabled?'online':'disabled')}</td><td>${esc((k.scopes||[]).join(', '))}</td><td>${fmtTime(k.last_used_at)}</td><td>${k.request_count??0} / ${k.estimated_tokens??0}</td><td>${k.token_quota==null?'不限':`${k.tokens_used??0} / ${k.token_quota}`}</td><td>${actions}</td></tr>`}).join('')}catch(e){status(e.message,'bad')}}
$('createKey').onclick=async()=>{try{const name=$('keyName').value.trim();if(!name)throw new Error('请输入名称');const days=Number($('keyDays').value||0);const d=await api('/api/admin/keys',{method:'POST',body:{name,expires_in_days:days||null,token_quota:$('keyQuota').value.trim()===''?null:Number($('keyQuota').value)}});$('newToken').textContent=d.token;$('newSecret').classList.remove('hidden');await loadKeys()}catch(e){status(e.message,'bad')}};
$('copyNew').onclick=()=>navigator.clipboard.writeText($('newToken').textContent||'');
async function copyKey(id){try{const d=await api(`/api/admin/keys/${encodeURIComponent(id)}/secret`);await navigator.clipboard.writeText(d.token);status('API Key 已复制','ok')}catch(e){status(e.message,'bad')}}
async function toggleKey(id,enabled){try{await api(`/api/admin/keys/${encodeURIComponent(id)}`,{method:'PATCH',body:{enabled}});loadKeys()}catch(e){status(e.message,'bad')}}
async function revokeKey(id){if(!confirm('确定永久撤销？'))return;try{await api(`/api/admin/keys/${encodeURIComponent(id)}`,{method:'DELETE'});loadKeys()}catch(e){status(e.message,'bad')}}
window.copyKey=copyKey;window.toggleKey=toggleKey;window.revokeKey=revokeKey;
async function loadRequests(){if(!key())return;try{const p=new URLSearchParams({limit:'100'});if($('rqSearch').value.trim())p.set('q',$('rqSearch').value.trim());if($('rqStatus').value)p.set('status',$('rqStatus').value);if($('rqModel').value.trim())p.set('model',$('rqModel').value.trim());const d=await api('/api/admin/requests?'+p);$('rqBody').innerHTML=(d.data||[]).map(r=>`<tr class="clickable" onclick="requestDetail('${esc(r.request_id)}')"><td>${fmtTime(r.recorded_at)}</td><td>${esc(r.request_type||'text')}</td><td>${pill(r.status)}</td><td>${esc(r.api_key_name||'-')}</td><td><code>${esc(r.requested_model)}</code></td><td>${r.attachments_count??0}</td><td>${fmtMs(r.timings?.first_token_ms)}</td><td>${fmtMs(r.timings?.total_ms)}</td><td>${r.usage?.total_tokens??0}</td></tr>`).join('')}catch(e){status(e.message,'bad')}}
$('rqGo').onclick=loadRequests;
async function requestDetail(id){try{const d=await api('/api/admin/requests/'+encodeURIComponent(id));$('rqDetail').innerHTML=`<b>${esc(d.request_id)}</b><pre>${esc(JSON.stringify(d,null,2))}</pre>`}catch(e){status(e.message,'bad')}}
window.requestDetail=requestDetail;
function renderDocs(){const b=location.origin;$('baseUrl').textContent=b+'/v1';$('docChat').textContent=`POST ${b}/v1/chat/completions\nAuthorization: Bearer YOUR_API_KEY\n\n{\n  "model":"default",\n  "messages":[{"role":"user","content":"请分析附件"}],\n  "attachments":[{"file_id":"file_xxx"}],\n  "stream":true\n}`;$('docFile').textContent=`POST ${b}/v1/files\n{\n  "filename":"photo.png",\n  "mime_type":"image/png",\n  "data_base64":"iVBOR...",\n  "purpose":"vision"\n}`;$('docImage').textContent=`POST ${b}/v1/images/generations\n{\n  "model":"gpt-image",\n  "prompt":"画一只在月光下的狐狸",\n  "response_format":"b64_json",\n  "attachments":[{"file_id":"file_optional_reference"}]\n}`}
function fileToBase64(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]||'');r.onerror=reject;r.readAsDataURL(file)})}
async function loadTestKeys(){const current=$('testKeySelect').value||'master';const d=await api('/api/admin/keys');testKeyCatalog=d.data||[];const options=['<option value="master">管理员 CHAT2API_API_KEY（默认）</option>'];for(const item of testKeyCatalog){if(!item.managed)continue;const usable=item.enabled&&!item.expired&&!item.revoked_at&&item.secret_recoverable;const state=item.revoked_at?'已撤销':item.expired?'已过期':!item.enabled?'已停用':!item.secret_recoverable?'旧 Key 无法恢复':'可用';options.push(`<option value="${esc(item.key_id)}" ${usable?'':'disabled'}>${esc(item.name)} · ${esc(item.prefix)} · ${esc(state)}</option>`)}$('testKeySelect').innerHTML=options.join('');if([...$('testKeySelect').options].some(o=>o.value===current&&!o.disabled))$('testKeySelect').value=current}
function maskKey(value){const v=String(value||'').trim();if(v.length<=12)return v?`${v.slice(0,4)}…`:'-';return`${v.slice(0,8)}…${v.slice(-4)}`}
async function resolveTestCredential(){const pasted=$('testKeyInput').value.trim();if(pasted)return{token:pasted,label:`手动粘贴 ${maskKey(pasted)}`,source:'pasted',key_id:null};const selected=$('testKeySelect').value||'master';if(selected==='master')return{token:key(),label:'管理员 CHAT2API_API_KEY',source:'admin',key_id:'master'};const item=testKeyCatalog.find(x=>x.key_id===selected);if(!item)throw new Error('所选测试 Key 不存在，请刷新后重试');if(!item.secret_recoverable)throw new Error('所选旧版 Key 没有可恢复密文，请手动粘贴完整 Key');const d=await api(`/api/admin/keys/${encodeURIComponent(selected)}/secret`);return{token:d.token,label:`${item.name} · ${item.prefix}`,source:'managed',key_id:selected}}
async function upload(file,purpose,testToken){const b64=await fileToBase64(file);return api('/v1/files',{method:'POST',key:testToken,body:{filename:file.name,mime_type:file.type||'application/octet-stream',data_base64:b64,purpose}})}
async function streamChat(model,prompt,attachments=[],testToken=key(),requestId=''){const started=performance.now();let first=null,totalText=0,meta=null,rawError='';const r=await fetch('/v1/chat/completions',{method:'POST',headers:{...headers(testToken),'Content-Type':'application/json',...(requestId?{'X-Chat2API-Request-ID':requestId}:{})},body:JSON.stringify({model,messages:[{role:'user',content:prompt}],attachments,stream:true,timeout:300})});if(!r.ok)throw new Error(`${r.status} ${await r.text()}`);const reader=r.body.getReader(),dec=new TextDecoder();let buf='';while(true){const {done,value}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;while((i=buf.indexOf('\n\n'))>=0){const block=buf.slice(0,i);buf=buf.slice(i+2);for(const line of block.split('\n')){if(!line.startsWith('data: '))continue;const payload=line.slice(6);if(payload==='[DONE]')continue;let d;try{d=JSON.parse(payload)}catch(_){continue}if(d.error){rawError=d.error.message||'stream error';continue}const c=d.choices?.[0]?.delta?.content||'';if(c){if(first===null)first=performance.now()-started;totalText+=c.length}if(d.usage||d.chat2api)meta={usage:d.usage,chat2api:d.chat2api}}}}if(rawError)throw new Error(rawError);return{request_id:requestId||null,first_token_ms:first,total_ms:performance.now()-started,response_chars:totalText,meta}}
function quality(result){if(result.status==='failed')return{grade:'failed',issues:['调用失败：'+(result.error||'unknown')]};if(result.status==='skipped')return{grade:'skipped',issues:[result.reason||'未实现']};const issues=[];if(result.first_token_ms!=null&&result.first_token_ms>30000)issues.push('首包延迟超过 30 秒');if(result.total_ms!=null&&result.total_ms>90000)issues.push('总耗时超过 90 秒');if(result.kind!=='image_generation'&&result.response_chars===0)issues.push('没有捕获到文本输出');if(!result.meta?.chat2api&&result.kind!=='image_generation')issues.push('缺少 chat2api diagnostics');return{grade:issues.length?'warning':'passed',issues}}
function row(r){return`<div class="timelineRow"><b>${esc(r.label)}</b><span>${pill(r.status)}</span><span>${esc(r.message||'')}</span><span>${fmtMs(r.first_token_ms)}</span><span>${fmtMs(r.total_ms)}</span><span>${esc(r.quality?.grade||'-')}</span></div>`}
function redraw(rows){$('timeline').innerHTML='<div class="timelineRow muted"><b>项目</b><span>状态</span><span>诊断</span><span>首包</span><span>总耗时</span><span>质量</span></div>'+rows.map(row).join('');$('testPass').textContent=rows.filter(x=>x.status==='passed').length;$('testWarn').textContent=rows.filter(x=>x.status==='warning'||x.status==='skipped').length;$('testFail').textContent=rows.filter(x=>x.status==='failed').length}
async function oneTest(kind,model,files,testToken){const labels={text:'文本',vision:'视觉理解',file:'文件理解',image_generation:'图片生成',voice_generation:'语音生成',voice_conversation:'语音对话'};const r={kind,label:labels[kind]||kind,status:'running',message:'执行中'};const started=performance.now();if(!kind.startsWith('voice_'))r.request_id=(kind==='image_generation'?'imgreq_':'req_')+crypto.randomUUID().replace(/-/g,'');try{if(kind.startsWith('voice_')){r.status='skipped';r.reason='当前版本尚未实现语音桥接';r.message=r.reason;return r}if(kind==='text'){Object.assign(r,await streamChat(model,'请只回复：chat2api 文本测试成功',[],testToken,r.request_id));}else if(kind==='vision'){const f=files.find(x=>x.type.startsWith('image/'));if(!f){r.status='skipped';r.reason='未选择图片附件';r.message=r.reason;return r}const u=await upload(f,'vision',testToken);Object.assign(r,await streamChat(model,'请识别附件图片，并用一句话描述主要内容。',[{file_id:u.id}],testToken,r.request_id));}else if(kind==='file'){const f=files.find(x=>!x.type.startsWith('image/'));if(!f){r.status='skipped';r.reason='未选择文档/文件附件';r.message=r.reason;return r}const u=await upload(f,'file-understanding',testToken);Object.assign(r,await streamChat(model,'请阅读附件，并用一句话概括核心内容。',[{file_id:u.id}],testToken,r.request_id));}else if(kind==='image_generation'){const image=files.find(x=>x.type.startsWith('image/'));const at=[];if(image){const u=await upload(image,'image-reference',testToken);at.push({file_id:u.id})}const t=performance.now();const d=await api('/v1/images/generations',{method:'POST',key:testToken,headers:{'X-Chat2API-Request-ID':r.request_id},body:{model:'gpt-image',prompt:image?'参考附件图片，生成一张风格明显不同但主体相关的新图片。':'生成一张简洁的蓝天白云测试图片。',response_format:'b64_json',attachments:at,timeout:600}});r.total_ms=performance.now()-t;r.image_count=d.data?.length||0;r.meta={usage:d.usage,chat2api:d.chat2api};if(!r.image_count)throw new Error('图片接口没有返回图片数据');}r.status='passed';r.message='调用完成'}catch(e){r.status='failed';r.error=String(e.message||e);r.message=r.error}finally{r.total_ms=r.total_ms??(performance.now()-started);r.quality=quality(r);if(r.status==='passed'&&r.quality.grade==='warning')r.status='warning'}return r}
$('startTest').onclick=async()=>{if(!key())return status('请先连接管理员主密钥','bad');let credential;try{credential=await resolveTestCredential()}catch(e){return status('测试 Key 读取失败：'+e.message,'bad')}const type=$('testType').value,model=$('testModel').value,files=[...$('testFiles').files],kinds=type==='all'?['text','vision','file','image_generation','voice_generation','voice_conversation']:[type];const rows=[];$('testState').textContent='运行中';redraw(rows);status(`正在使用：${credential.label}`,'ok');const startedAt=new Date().toISOString(),start=performance.now();for(const kind of kinds){const pending={kind,label:{text:'文本',vision:'视觉理解',file:'文件理解',image_generation:'图片生成',voice_generation:'语音生成',voice_conversation:'语音对话'}[kind],status:'running',message:'执行中'};rows.push(pending);redraw(rows);const done=await oneTest(kind,model,files,credential.token);rows[rows.length-1]=done;redraw(rows)}const failed=rows.some(x=>x.status==='failed'),warn=rows.some(x=>x.status==='warning'),passed=rows.some(x=>x.status==='passed');const reportStatus=failed?'failed':warn?'warning':passed?'passed':'skipped';const runId='testrun_'+Date.now().toString(36)+Math.random().toString(36).slice(2,7);const report={run_id:runId,test_type:type,status:reportStatus,model,started_at:startedAt,finished_at:new Date().toISOString(),duration_ms:performance.now()-start,summary:`${rows.filter(x=>x.status==='passed').length} passed, ${rows.filter(x=>x.status==='warning').length} warning, ${rows.filter(x=>x.status==='failed').length} failed, ${rows.filter(x=>x.status==='skipped').length} skipped`,results:rows,quality:{api_key_label:credential.label,api_key_source:credential.source,api_key_id:credential.key_id,bugs:rows.filter(x=>x.status==='failed').map(x=>x.message),latency_warnings:rows.flatMap(x=>x.quality?.issues||[]).filter(x=>/延迟|耗时/.test(x))}};try{await api('/api/admin/tests',{method:'POST',body:report});$('testState').textContent=reportStatus;await loadTests()}catch(e){status('保存测试报告失败：'+e.message,'bad')}};
async function loadTests(){if(!key())return;try{await loadTestKeys();const d=await api('/api/admin/tests?limit=50');$('testHistory').innerHTML=(d.data||[]).map(r=>`<tr><td>${fmtTime(r.recorded_at||r.finished_at)}</td><td>${esc(r.test_type)}</td><td>${pill(r.status)}</td><td>${esc(r.model||'-')}</td><td class="keyBadge" title="${esc(r.quality?.api_key_label||'-')}">${esc(r.quality?.api_key_label||'-')}</td><td>${fmtMs(r.duration_ms)}</td><td>${esc(r.summary||'')}</td></tr>`).join('')||'<tr><td colspan="7" class="muted">暂无测试记录</td></tr>'}catch(e){status(e.message,'bad')}}
async function loadMediaStorage(){
  try{
    const d=await api('/api/admin/media-storage');
    $('mediaBackend').value=d.backend||'local';
    $('mediaAutoArchive').checked=d.auto_archive!==false;
    $('mediaPublicBase').value=d.public_base_url||'';
    $('mediaS3Bucket').value=d.s3?.bucket||'';
    $('mediaS3Region').value=d.s3?.region||'us-east-1';
    $('mediaS3Endpoint').value=d.s3?.endpoint||'';
    $('mediaS3Key').value='';$('mediaS3Secret').value='';
    $('mediaStorageStatus').textContent=d.backend==='s3'?'S3 已配置（上传权限需实机验证）':'本地转存已就绪';
    $('mediaSummary').textContent='归档 '+(d.count||0)+' 项，原图 '+(Number(d.original_bytes||0)/1048576).toFixed(2)+' MiB';
    const files=await api('/api/admin/media-storage/files');
    $('mediaStoredRows').innerHTML=(files.data||[]).map(f=>
      '<tr><td>'+esc(fmtTime(f.created_at))+'</td><td>'+esc(f.request_id)+'</td><td>'+esc(f.mime)+
      '</td><td>'+esc(f.width)+'×'+esc(f.height)+'</td><td>'+Math.round(f.size/1024)+' KiB</td><td>'+esc(f.backend)+'</td><td><button class="action danger" data-media-delete="'+esc(f.id)+'">删除</button></td></tr>').join('')
      ||'<tr><td colspan="7" class="muted">暂无归档记录</td></tr>';
    document.querySelectorAll('[data-media-delete]').forEach(btn=>{
      btn.onclick=async()=>{
        const id=btn.dataset.mediaDelete;
        if(!/^med_[a-zA-Z0-9_]{12,90}$/.test(id||'')||!confirm('删除转存文件与缩略图？删除后访问链接立即失效。'))return;
        try{await api('/api/admin/media-storage/files/'+encodeURIComponent(id),{method:'DELETE'});await loadMediaStorage()}
        catch(e){$('mediaStorageStatus').textContent='删除失败：'+e.message;}
      };
    });
    const generated=await api('/api/admin/media-storage/generated-files');
    const items=generated.data||[];
    $('generatedFileSummary').textContent='已转存 '+items.length+' 项（仅展示最近记录），文件先归档再交付；图片管理仍在上表。';
    $('generatedFileRows').innerHTML=items.map(f=>
      '<tr><td>'+esc(fmtTime(f.created_at))+'</td><td>'+esc(f.request_id)+'</td><td>'+esc(f.filename)+
      '</td><td>'+esc(f.mime)+'</td><td>'+Math.round(f.size/1024)+' KiB</td><td>'+esc(f.backend)+
      '</td><td><button class="action danger" data-generated-delete="'+esc(f.id)+'">删除</button></td></tr>').join('')
      ||'<tr><td colspan="7" class="muted">暂无归档的生成文件</td></tr>';
    document.querySelectorAll('[data-generated-delete]').forEach(btn=>{
      btn.onclick=async()=>{
        const id=btn.dataset.generatedDelete;
        if(!/^gen_[a-zA-Z0-9_-]{12,90}$/.test(id||'')||!confirm('删除生成文件？已有下载链接将立即失效。'))return;
        try{await api('/api/admin/media-storage/generated-files/'+encodeURIComponent(id),{method:'DELETE'});await loadMediaStorage()}
        catch(e){$('mediaStorageStatus').textContent='文件删除失败：'+e.message;}
      };
    });
  }catch(e){$('mediaStorageStatus').textContent='加载失败：'+e.message;}
}
$('mediaReloadSettings').onclick=loadMediaStorage;
$('mediaTestSettings').onclick=async()=>{
  $('mediaStorageStatus').textContent='正在执行存储写入/读取验证…';
  try{
    const d=await api('/api/admin/media-storage/test',{method:'POST'});
    $('mediaStorageStatus').textContent=d.ok?'真实存储读写测试通过 · '+d.backend:'存储读写测试未通过';
  }catch(e){$('mediaStorageStatus').textContent='真实存储读写测试失败：'+e.message;}
};
let mediaBackfillOffset=0;
$('mediaBackfillThumbnails').onclick=async()=>{
  const button=$('mediaBackfillThumbnails');button.disabled=true;
  let scanned=0,repaired=0,failed=0,finished=false;
  try{
    for(let round=0;round<5;round++){
      $('mediaStorageStatus').textContent='正在检查历史缩略图… 已扫描 '+scanned+' 项';
      const d=await api('/api/admin/media-storage/backfill-thumbnails?limit=50&offset='+mediaBackfillOffset,{method:'POST'});
      scanned+=Number(d.scanned||0);repaired+=Number(d.repaired||0);failed+=Number(d.failed||0);
      if(d.next_offset==null){finished=true;mediaBackfillOffset=0;break;}
      mediaBackfillOffset=d.next_offset;
    }
    $('mediaStorageStatus').textContent='历史缩略图检查：扫描 '+scanned+' 项，补全 '+repaired+' 项，失败 '+failed+' 项'+(finished?'，本轮已全部扫描':'；可再次点击继续下一批');
  }catch(e){$('mediaStorageStatus').textContent='历史缩略图补全失败：'+e.message;}
  finally{button.disabled=false;}
};
$('mediaSaveSettings').onclick=async()=>{
  try{
    const body={backend:$('mediaBackend').value,auto_archive:$('mediaAutoArchive').checked,
      public_base_url:$('mediaPublicBase').value.trim(),s3:{
        bucket:$('mediaS3Bucket').value.trim(),region:$('mediaS3Region').value.trim(),
        endpoint:$('mediaS3Endpoint').value.trim(),access_key_id:$('mediaS3Key').value.trim(),
        secret_access_key:$('mediaS3Secret').value.trim()}};
    await api('/api/admin/media-storage',{method:'PUT',body});
    $('mediaStorageStatus').textContent='已保存配置';await loadMediaStorage();
  }catch(e){$('mediaStorageStatus').textContent='保存失败：'+e.message;}
};
$('connect').disabled=true;$('refresh').disabled=true;document.querySelectorAll('.nav button').forEach(button=>button.disabled=true);
(async()=>{
  let session=null,sessionCheckSucceeded=false;
  try{
    const response=await fetch('/api/admin/auth/session',{credentials:'same-origin',cache:'no-store'});
    if(!response.ok)throw new Error('session lookup failed');
    session=await response.json();sessionCheckSucceeded=true;
  }catch(_){}
  if(sessionCheckSucceeded&&session&&session.authenticated&&session.role!=='super_admin'){
    try{sessionStorage.removeItem('chat2api_admin_key')}catch(_){}
    $('adminKey').value='';$('adminKey').disabled=true;$('connect').hidden=true;$('refresh').hidden=true;
    document.querySelectorAll('.nav button:not([data-cview])').forEach(button=>{button.hidden=true;button.disabled=true});
    document.querySelectorAll('.nav button[data-cview]').forEach(button=>{button.disabled=false});
    const defaults={system_admin:'client-policy',api_key_admin:'api-keys',plugin_admin:'plugins',support:'model-entitlements',billing_admin:'user-pricing',user_admin:'admin-users'};
    if(defaults[session.role])location.hash=defaults[session.role];
    status('已识别委派管理员 Session：'+session.username+' · '+session.role,'ok');
  }else if(sessionCheckSucceeded){
    $('connect').disabled=false;$('refresh').disabled=false;
    document.querySelectorAll('.nav button').forEach(button=>button.disabled=false);
    const start=(location.hash||'#'+INITIAL_VIEW).slice(1);show(titles[start]?start:'overview');
  }else{
    try{sessionStorage.removeItem('chat2api_admin_key')}catch(_){}
    $('adminKey').value='';$('connect').disabled=false;$('refresh').disabled=true;
    document.querySelectorAll('.nav button:not([data-cview])').forEach(button=>button.disabled=true);
    document.querySelectorAll('.nav button[data-cview]').forEach(button=>{button.disabled=false});
    $('view-overview').classList.add('active');
    status('无法验证管理员 Session；已阻止自动使用缓存主密钥，请检查服务后重新登录','bad');
  }
})();
</script>
<dialog id="adminPasswordDialog">
  <div class="dialogHead"><b>修改管理员密码</b><button class="action" id="adminPasswordClose" type="button">关闭</button></div>
  <div class="dialogBody">
    <p class="muted">新密码至少 12 位。保存后当前管理员的所有会话会立即撤销，需要使用新密码重新登录。</p>
    <label class="field">当前密码<input id="adminPasswordCurrent" type="password" autocomplete="current-password" maxlength="256"></label>
    <label class="field">新密码<input id="adminPasswordNew" type="password" autocomplete="new-password" minlength="12" maxlength="256"></label>
    <label class="field">确认新密码<input id="adminPasswordConfirm" type="password" autocomplete="new-password" minlength="12" maxlength="256"></label>
    <div id="adminPasswordStatus" class="muted"></div>
  </div>
  <div class="dialogFoot"><button class="action good" id="adminPasswordSave" type="button">保存并退出会话</button></div>
</dialog>
</body></html>'''


def admin_response(initial_view: str = "overview") -> HTMLResponse:
    view = initial_view if initial_view in {"overview", "modes", "keys", "requests", "media-storage", "docs", "playground", "control-admins", "client-policy", "registration-invitations", "tenants", "model-entitlements", "plugins", "quota-ledger", "api-keys"} else "overview"
    return HTMLResponse(ADMIN_HTML.replace("__INITIAL_VIEW__", view), headers={"Cache-Control": "no-store"})
