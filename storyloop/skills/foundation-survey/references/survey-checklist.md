# 按技术栈的探查清单

先看什么、在哪找、找到后写进哪份文档。适用的清单按仓库实际技术栈组合使用。

## 通用（任何仓库）

| 看什么 | 在哪 | 写进 |
|---|---|---|
| 项目定位、运行方式 | `README*`、`docs/`、`AGENTS.md`、`CLAUDE.md`、`acceptance/AGENT_GUIDE.md` | README、architecture |
| 可变区与集成点 | `.storyloop/config.json` 的 `profile`、`acceptance/storyloop-settings.json` | modules |
| 已实现能力 | `openspec/specs/*/spec.md`、`openspec/changes/*/proposal.md` | capabilities |
| CI 与脚本 | `.github/workflows`、`Jenkinsfile`、`scripts/`、`Makefile` | testing |
| 验收工具 | `acceptance/tools/`、`acceptance/smoke/`、`acceptance/ui/` | testing、building-blocks |
| 被忽略但会生成的东西 | `.gitignore`、settings 的 `scratch_paths` | testing |

## Maven / Spring Boot

| 看什么 | 在哪 | 写进 |
|---|---|---|
| 模块树与依赖管理 | 根 `pom.xml` 的 `<modules>`、`<dependencyManagement>`、各模块 `pom.xml` | modules |
| 默认跳过测试、surefire 筛选 | 根 `pom.xml` 的 `<properties>`（`maven.test.skip`）、surefire `<groups>`/`<excludedGroups>` | testing |
| 入口与自动装配 | `@SpringBootApplication` 类、`META-INF/spring/*.imports`、`*AutoConfiguration` | architecture |
| 配置与 profile | `application*.yml`：只记键名与文件，不记值；`spring.profiles.active` 的来源 | architecture |
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
| 环境变量 | `.env*`：只记键名与用途（端口、代理目标、是否加密） | architecture |
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
| 配置 | settings 模块、`.env`：只记键名 | architecture |

## 记录方式

- 每条结论后面括注来源路径，例如 `（backend/ruoyi-common/ruoyi-common-web/.../GlobalExceptionHandler.java）`。
- 看不到源码只能推断的，写 `[待确认]`，并在 README 汇总。
- 上游文档与代码不一致时以代码为准，并记一笔差异。
