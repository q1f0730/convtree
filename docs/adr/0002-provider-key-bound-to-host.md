# 密钥绑定主机；Host 头白名单；默认预设 Inception Mercury

审计 P0-1：只改 baseUrl 就能把已存 key 送到任意服务器。决定：**key 与 base_url 的主机绑定**——保存时若 chat（或 embed）base_url 的主机变了而本次没带新 key，就清空旧 key；嵌入只在 embed_base_url 为空或与聊天同主机时才回落聊天 key。另加 Host 头白名单中间件（防 DNS rebinding）：默认允许 localhost/127.0.0.1/::1/testserver，再加环境变量 `CONVTREE_ALLOWED_HOSTS`（逗号分隔，支持 `*.example.com`），部署时写入 InstaBox 公网主机名。配置优先级显式化：配置文件字段 > 环境变量（含 `.env`）> 服务商预设默认（修 P2-1）。默认服务商预设改为 Inception Mercury（`https://api.inceptionlabs.ai/v1`，`mercury-3`），无 embeddings 端点 ⇒ 未配嵌入时纯 BM25。

**Considered Options**：给写接口加 token 鉴权（更彻底，但前端同源取 token 在公网部署下等于没有，且需要登录 UX；留作后续）。
**Consequences**：用户换服务商时必须重填 key——这是有意的。
