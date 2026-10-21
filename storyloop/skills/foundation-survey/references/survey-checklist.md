# 按技术栈的探查清单

先看什么、在哪找、找到后写进哪份文档。适用的清单按仓库实际技术栈组合使用。

## 通用（任何仓库）

| 看什么 | 在哪 | 写进 |
|---|---|---|
| 项目定位、运行方式 | `README*`、`docs/`、`AGENTS.md`、`CLAUDE.md`、`acceptance/AGENT_GUIDE.md` | README、architecture |
| 可变区与集成点 | `.storyloop/config.json` 的 `profile`、`acceptance/storyloop-settings.json` | modules |
| 实际行为与应有能力 | 实现/测试定义，以及活规格/变更提案；规划不能单独证明实现 | capabilities |
| 运行单元与身份边界 | 构建/CLI 入口、调度注册、身份识别/授权/拒绝消费代码 | modules、architecture |
| 行为开关 | 公开键定义、消费代码与默认分支；不读秘密运行配置 | architecture |
| 贡献规范及需求路由 | 已有 CONTRIBUTING/提交规范、模块/规格/实现之间的证据 | extension |
| 测试身份及隔离 | 角色别名、fixture 工厂、创建/重置/清理定义；不读真实账号 | testing |
| 基线与观测版本 | payload、已有非敏感 BASE 元数据、Git HEAD/工作树状态 | 八文档头、README |
| CI 与脚本 | `.github/workflows`、`Jenkinsfile`、`scripts/`、`Makefile` | testing |
| 验收工具 | `acceptance/tools/`、`acceptance/changes/`（变更验收）、`acceptance/smoke/`（系统冒烟）、`acceptance/ui/` | testing、building-blocks |
| 被忽略但会生成的东西 | `.gitignore`、settings 的 `scratch_paths` | testing |

## Maven / Spring Boot

| 看什么 | 在哪 | 写进 |
|---|---|---|
| 模块树与依赖管理 | 根 `pom.xml` 的 `<modules>`、`<dependencyManagement>`、各模块 `pom.xml` | modules |
| 默认跳过测试、surefire 筛选 | 根 `pom.xml` 的 `<properties>`（`maven.test.skip`）、surefire `<groups>`/`<excludedGroups>` | testing |
| 入口与自动装配 | `@SpringBootApplication` 类、`META-INF/spring/*.imports`、`*AutoConfiguration` | architecture |
| 配置与 profile | 公开配置示例、键定义、`@ConfigurationProperties` 与消费代码；秘密运行配置不可读 | architecture |
| 请求链路 | 过滤器/拦截器、认证框架（Sa-Token、Spring Security）、`@SaCheckPermission` 之类的权限注解、`@Validated` 分组、全局异常处理、统一返回类型 `R<T>` | architecture |
| 数据访问 | MyBatis-Plus 的 `BaseMapperPlus`、`PageQuery`/`TableDataInfo`、多租户与逻辑删除插件、`MetaObjectHandler` 审计字段填充 | architecture、building-blocks |
| 接口清单 | `@RestController` + `@RequestMapping` 的类：路径、方法、权限串 | api |
| 扩展机制 | 代码生成器模板（`fm/*.ftl`）、参考模块（demo）、菜单 SQL 模板 | extension |
| 通用服务 | 字典、用户、部门、OSS、导出（ExcelUtil）、缓存工具、对象转换（MapstructUtils） | building-blocks |
| 跨模块接口 | `*-api` 模块里的接口与 DTO | modules、extension |
| 数据库脚本 | `script/sql/*.sql`、业务 SQL 目录、菜单 ID 区段 | extension |

## Gradle

同 Maven；模块树在 `settings.gradle(.kts)`，测试筛选看 `test { useJUnitPlatform { includeTags } }`。

## Node / Vite / Vue

| 看什么 | 在哪 | 写进 |
|---|---|---|
| 依赖与脚本 | `package.json` 的 `scripts`、`devDependencies`（是否有 vitest、@vue/test-utils、playwright） | testing |
| 环境变量 | 不打开 `.env*`；查 `import.meta.env` 等消费代码及公开键定义，说明用途及未知部署值 | architecture |
| 请求封装 | `src/utils/request.ts`：拦截器、token 头、错误处理、加解密开关 | architecture、building-blocks |
| 路由与权限 | `src/router/`、`src/permission.ts`、`src/store/modules/permission`：动态路由如何从后端菜单生成、`v-hasPermi` 指令 | architecture、extension |
| 字典与提示 | `useDict`、`proxy?.$modal`、`DictTag` 组件 | building-blocks |
| 页面骨架 | 一个参考页面（`views/<module>/index.vue`）的结构：查询区、表格、对话框表单 | extension |
| 自动生成文件 | unplugin 的 `auto-imports.d.ts`、`components.d.ts` | testing（scratch 提示） |

## Python

| 看什么 | 在哪 | 写进 |
|---|---|---|
| 包与入口 | `pyproject.toml`（scripts、packages）、`__main__.py` | modules、architecture |
| 测试 | `tests/`、`pytest.ini`/`pyproject` 的 `[tool.pytest]`、`unittest` 布局 | testing |
| 配置 | 安全的键定义、消费代码及公开示例；不打开 `.env`/凭证/秘密运行配置 | architecture |

## 记录方式

- 按 [source-and-sampling.md](source-and-sampling.md) 为事实分配局部 ID、三种来源状态、独立核验结果、相对路径/符号、观测完整 SHA 与限制；路径存在不等于支持结论。
- 看不到源码时区分名称推测、文档声称、待确认；不适用需要理由与已探查范围，不能用它掩盖未查清。
- 文档/活规格与代码不一致时记录双方证据和冲突；代码不是取消既有需求的理由。
- Git tracked 清单不包含被忽略路径，不代表该路径不存在；安全范围内核实已有 BASE 元数据，不可读就写待确认。
- README 汇总七类覆盖、未知及冲突；冻结全部正文事实后用一次种子抽样，重新打开来源并保留失败/修正/原样本复核。读取测试定义不等于运行通过。
