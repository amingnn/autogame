- 由原项目 https://github.com/xxyz30/skyland-auto-sign 精简而来
- 自动化前需运行一次存入登陆信息

当前实现保留原项目的手机号密码登录接口。账号配置位于本机根目录 `config.yaml` 的
`tasks.skyland_sign.account`。签到时优先使用 `data/skyland_sign/token.txt` 中的缓存
Token，只有认证失效时才重新登录并更新缓存；未配置账号时无法刷新失效 Token。
