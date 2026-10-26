# 接入规范草案

未初始化、需人工补全。先审阅并提交，再使用原 init 锁定；初始化不代表 readiness READY。

- 填写 module_roots、test_roots 和 integration_paths，明确哪些模块可写。
- acceptance/tools、此规范、settings 和 onboarding.json 必须留在底座保护范围。
- 单元、集成、验收、系统冒烟、静态检查分别选择真实执行命令；不得用空通过脚本代替。
- 冒烟只接收 manifest 指定的 case，不自动发现目录，也不附加 acceptance/ui。
- source_files 只能填写明确安全文件；不扫描目录，不复制 .env、密钥或凭证。
- 依赖需预热；依赖缓存显式声明，构建输出使用每次独占 scratch，不下载或安装。
- 明确 build/start/ready/run_case/cleanup；不用的阶段需 not_applicable 及理由。
- 请人工确认 settings 中的 `base_build_commands`：空数组明确表示项目没有构建步骤；有构建步骤时填写 argv 命令。
- 数据隔离、业务断言、只读健康探针和必要的权限须由项目确定。本模板不配置数据库或登录。
- 运行 0 表示实际执行全绿，1 仅表示行为断言失败，2 表示环境/配置/清理错误或零执行。
- 完成后将 onboarding.json 的 reviewed 标记为 true 并提交；后续修改保护文件走 BASE。

## 项目约定（人工填写）

模块所有权、测试目录、集成入口、命令与缓存寻址、只读健康条件、仍有的 readiness 缺口。
