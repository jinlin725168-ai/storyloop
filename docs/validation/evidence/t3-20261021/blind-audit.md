# T3 独立盲复核原始记录

审计时间：2026-10-21 07:58:28 UTC。审计者：独立子代理 `/root/t3_blind_audit`。

范围：公开 fixture `/tmp/storyloop-t3-public-20261021/source`；Claude/Codex 各自的自然 `last-survey.json`、实际安装的八份 `openspec/foundation/*.md`、非敏感调用请求、准备记录和经主代理明确授权的公开 BASE 元数据。审计标准为 `bce1a8661b0ebbf7e100d3a782883993d103b368` 的 foundation-survey 1.1.0、source-and-sampling 质量约定 1，以及八文档模板。观察提交均为 `f65e8f7901dd1da578a72ce8a9aadb9f2a284dfa`。

操作边界：只读检查项目、产物与来源；未改写自然产物；未调用外部模型；未再委派；未打开凭证或私密配置；未执行来源脚本或项目测试。通过自行编写的标准库只读 Python 进行摘要和抽样重放。仅此报告按主代理后续明确授权写入 `/tmp/storyloop-t3-blind-audit.md`。本审计发生在自然 survey 之后，不能回填成自然产物原先已有的独立复核。

## Claude 上报原文

Claude 盲复核原始结论（只读，无改写、无外部模型、无重跑）：八份文件齐备，复制内容逐字等于 last-survey.json；七类映射和模板覆盖均可定位；源仓库8个tracked文件均实际打开；观测SHA f65e8f7901dd1da578a72ce8a9aadb9f2a284dfa、BASE版本1.0.0、module_roots app / integration_paths pyproject.toml正确。无服务/前端/身份/数据库/开关等适用判断与极小源码相符。明确区分测试定义与运行，未冒称测试通过；历史手册确实缺失且保留待确认；精确匹配代码和测试 vs 活规格SHOULD前缀匹配、README前缀说法均被保留，未用代码裁掉需求；只有一个复用函数，未凑数量。

质量约定1不能判全通过，具体发现：
1. 自然产物事实接口不完整：extension.md:27-29 三条不同路由复用 extension:F003；路由行缺独立来源状态/核验/观测SHA/限制。正文还有未编号的实质结论，如 architecture.md:11 配置/环境变量不存在，:34 部署待确认；extension.md:14 修改路径，:17 保护范围；building-blocks.md:14 无验收资产；testing.md:24 StoryLoop回归元数据未知、:29忽略项。它们没有进入“全部事实”的候选池；不能把21项池的重放成功等同事实全面抽检。
2. 自然产物未知汇总遗漏：README.md:24-28只汇总 testing:F003 推断，漏 extension:F001（明确标推断）、extension:F003 路由推断、architecture:F002 的KeyError阅读推断；architecture.md:34部署待确认、testing.md:24回归元数据待确认也未汇总。api:F002冲突仅间接指 capabilities:F003，主要冲突仍可追溯。
3. 自然产物定位错误：extension.md:14明说 tests/test_catalog.py 第8行断言前缀al返回空，实际是第7行，第8行是alpha精确命中。capabilities.md:11 的 tests:8-9 没覆盖它声称的al断言（文件仅8行）；:21 tests:8 同理。api.md:11 的8-9部分越界；testing.md:21 fixture在源第6行，引用7行是断言。真实行为结论大体正确，但这些精确证据位置不可靠。
4. 上游固定提示/元数据冲突与自检漏检：modules.md:25 modules:F003、extension.md:17将tests、openspec等其余全部称受保护底座；实际baseline.foundation_paths与protected_paths仅.gitignore、README.md、pyproject.toml。bce1a86 role_instructions.py survey又确实写everything else is protected foundation，故归因为固定概括和实际精确元数据不一致，不能仅归咎Claude编造。README.md:43把整条modules:F003记“相符”，只核对两个payload数组，未核对保护范围后半句。这个样本必须保留失败/冲突，不能保持“无不符项”。BASE source_head主动不读而写待确认本身符合允许的安全降级；独立复核已从授权公开baseline确认SHA，但没有回填自然产物。

抽样独立重放：将正文已有的唯一ID展开为21对候选，SHA-256确为2e888066e1c94234b03d1772559b0ec795ce37952e2504fd18daa2fee07eef24；种子c5578133fa7ada6af2ee998a75537773格式合法；N21/D7/抽7正确；全局hash序恰为extension:F002、capabilities:F002、testing:F002、architecture:F001、building-blocks:F001、api:F002、modules:F003。其余6样本与公开来源相符，modules:F003前半符合、后半存在上述冲突/漏核。README:32压缩列出全21个唯一ID可展开重放，但宣称按字节序的展示顺序实际不是字节序。候选冻结时点/一次随机生成/作者是否逐条重新打开来源仅靠最终JSON无法独立证明，本审计只确认可重放及来源比对，不追认其执行轨迹。

自检/独立边界：自然产物明确写自检、未做独立复核、随机抽样不证明完整性，边界表述合格。我的检查构成后续独立发现，不应改写成Claude原先已独立通过。结论：主要仓库事实与冲突识别有效；抽样数学可重放；事实入池完整性、ID唯一性、未知汇总、证据定位和整条样本核验尚未满足质量约定1。SURVEYED不构成通过证明。等待Codex路径后将独立检查同样项目。

## Codex 最终原始报告

Codex 盲复核结论：八份文件齐备，实际安装内容逐字等于自然 last-survey.json；七类映射及模板覆盖均可定位。八个原始 tracked 来源文件全部实际打开，内容哈希逐项等于公开 baseline.files，并与公开 source 内容一致。观测 Git HEAD、BASE source_head 均为 f65e8f7901dd1da578a72ce8a9aadb9f2a284dfa；BASE 版本1.0.0，payload module_roots=[app] / integration_paths=[pyproject.toml]。日期、技能/质量约定版本、工作树限定、每文档观测版本均存在，正文明确继承文档完整 SHA 和工作树限定。

主要事实有效：lookup 的精确相等、迭代顺序、保留原对象、新列表、无显式输入校验/异常转换均有实际源码依据；没有虚构 HTTP、服务、前端、数据库或权限体系。代码/测试的精确行为与活规格 SHOULD 前缀要求、README 前缀说法均清楚保留，且区分“测试断言存在”和“测试已运行”。docs/retired-runtime.md 以及 docs 目录确实缺失，产物保留历史内容待确认。无贡献规则、安全配置未知、外部部署、回归命令及上游来源的限制基本明确。复用清单如实列一个函数、一个实际测试参考，没有将一个函数拆成多个积木。

### Codex 抽样与样本核对

28个编号事实均为唯一 `(文档路径, ID)`，七正文都有事实。README.md:50-77 的完整候选清单实际使用28个 Tab，无字面反斜杠 t；清单文本逐字等于按路径/ID UTF-8 字节序排序并以 LF 结束的规范编码。独立计算得到：

- 候选摘要：72e2e19dbe3bd2ef9a8198c052530592556a3b18e96b1fb8dbd66d38144dd5c5。
- 种子：507d7010a4d6e86166902ebb36b3a8a8，格式为合法32位小写hex。
- N=28、D=7、sample_size=7，符合 min(N,max(7,D,ceil(10%N)))。
- 全局hash顺序：testing:F003、modules:F002、extension:F001、capabilities:F003、api:F001、architecture:F006、building-blocks:F001，逐项与自然产物记录一致。

独立重新打开各样本来源后的判断：

| 样本 | 独立核对 | 边界 |
|---|---|---|
| testing:F003 | 相符：测试直接导入函数、方法内局部构造合成数据，无服务/账户生命周期 | 仅静态检查，不是执行通过 |
| modules:F002 | 相符：测试包、unittest、lookup 导入和测试名正确 | tests/__init__.py 实际为空 |
| extension:F001 | 相符：建议的函数/测试起点符合现有布局，并标推断 | 不是已存在生成器或历史扩展提交 |
| capabilities:F003 | 相符：代码/测试 vs 活规格/README 前缀冲突真实 | “冲突记录相符”不等于规格实现合规 |
| api:F001 | 相符：两个可位置/关键字参数、无默认/注解、新列表保留匹配原对象 | 未添加字符串类型限制或复制保证 |
| architecture:F006 | 相符：当前公开实现中无配置消费、开关、前端 | 外部部署/包装保持未知 |
| building-blocks:F001 | 相符：实际函数和tests:8调用正确 | 不是前缀搜索实现 |

七条样本均与其公开来源及相应静态限制相符。摘要/选样可以重放，不证明事实全集完整，也不能仅凭结果文件证明作者先冻结再一次随机生成或逐条重新打开来源的真实执行轨迹。自检身份、未独立复核、随机样本不证明完整性/统计准确率等说明均明确。README中的“baseline independently read”在上下文表示从元数据独立于payload获取，不是声称有第二位复核者。

### Codex 自然缺陷：将不同哈希对象比较成来源异常

README.md:41 宣称请求技能sha 2d2e25d3e5128f44cddbac16d12523329385db40a0d74f79f1f1fc8e1efc8cb6 与本地 SKILL.md 单文件sha 59398379c962dfdf4ab29a43e5b719c9fa0f011717470a6a1fc21c001a782f10 不同，因而登记 invocation provenance discrepancy / 待确认；README.md:111 又把解释该差异列为未完成事项。这是自然产物中的假异常。

根因已核实：bce1a86 storyloop/skills.py 的 instruction_skill_snapshot、_snapshot（sha256计算）、_skill_text（SKILL.md及references拼接）表明快照sha针对完整投喂文本，不是 SKILL.md 单文件。独立重放拼接规则：SKILL.md 正文 + 每个按文件名排序的 references/*.md，分别前置两个LF、`<!-- references/<name> -->`、LF，再UTF-8 SHA-256；不导入或执行项目代码。Claude/Codex两项目的 .claude/.codex 共四个副本均得到2d2e25d3e5128f44cddbac16d12523329385db40a0d74f79f1f1fc8e1efc8cb6，准确等于实际请求快照与准备记录。并且git bce1a86的 SKILL.md 原始字节也与本地相同，单文件摘要593983...不存在行尾差异。

因此实际快照与已安装完整技能包可以匹配。请求path指向入口文件而sha覆盖完整技能正文及references；不能以不同对象的hash不同推断安装/调用来源不一致。此处“待确认”虽避免捏造确定原因，却把可从真实引擎实现解释的正常差异登记成问题；应保留为自然产物错误发现，不回写自然产物，也不将它记为真实验收环境冲突。

### Codex 继承的上游边界冲突与定向检查漏项

modules.md:28 modules:F003 说其他repository areas全部是protected foundation；extension.md:25 extension:F003 明确说tests/specifications remain protected；extension.md:19、:37进一步把规格/测试导航到protected流程。实际公开 BASE 的foundation_paths/protected_paths仅 .gitignore、README.md、pyproject.toml；请求role_profile.forbidden_paths另列.storyloop/**、openspec/lessons/**，没有把tests和全部openspec列成同类保护底座。

固定版本 role_instructions.py 的survey提示确实写了“everything else is protected foundation”；因此这不是只能归咎模型的凭空编造。应区分“这次survey不允许写任何文件”“固定提示对底座的概括”“引擎实际精确保护路径与测试边界”。Codex已经读取并引用safe baseline，但只用版本/source_head，没有比对这些路径差异，因而以已核对状态保留了上游概括。该事实不在本轮七条随机样本内，不应编造成随机样本失败；它是定向边界复核发现，不能被七条样本相符覆盖。

### Codex 事实入池与未知汇总的剩余缺口

编号事实28项的候选清单完整、唯一且可重放；但“全部正文事实均入池”的更强要求仍有缺口：extension.md:11 的无生成器/模板、生成器约定待确认和包级导出结论没有明确自己的ID；modules.md:22的无跨模块依赖强制规则没有ID；testing.md:12对未覆盖前缀成功、异常、重复/顺序、对象身份断言的清单没有ID（其中无skip部分复述testing:F002，其余缺失覆盖结论并非整条复述）。这些实际阅读所得结论未单独入池。

extension:F004以一个泛化“路由表遵循实际依赖”的结论概括五种不同路由；虽没有像Claude那样出现字面重复ID，但仍以一个ID打包多个可分别核对的导航建议。extension:F003同时打包贡献规则缺失、写边界和外部政策未知，导致“缺贡献文件已核对”容易掩盖“保护路径不一致”问题。这种原子性不足与复合结论核对风险应登记，而不是只看行ID唯一。

README大部分推断、冲突、未知已可追溯，较完整；但extension.md:11“generator convention待确认”未在README的未知/未覆盖项中指回，architecture:F006的外部包装/部署未知主要由README:38泛述环境未运行核验，未直接链接该事实。后一项属于追溯细节，不能与完全未说明部署未知混同。

### Codex 结论

主要仓库事实、核心冲突识别、历史缺失处理、BASE/观测区分、测试定义/运行区分、自检/独立边界、摘要和选样重放均有效；没有发现Claude那批测试行号偏移。七条实际样本相符。但调用技能摘要的假异常、固定提示与精确保护路径的冲突未识别，以及少量事实编号/原子性和未知汇总缺口，使自然产物不能判为质量约定1全部通过。SURVEYED只证明引擎安装/提案路径，不改变这一质量判断。

## 跨产物归因边界

此fixture只有一次Claude和一次Codex自然survey，不能据此推广模型总体准确率或稳定通过率。两份自然产物均保留，不进行行为重试或人工修补后冒称原验收通过。共同的固定提示/精确边界矛盾属于上游上下文问题；Claude样本复合结论漏核、ID重复/漏编和证据行号错误，Codex的不同哈希对象误判与少量事实漏编，属于各自自然产物及自检发现。此次额外读到的公开BASE和引擎实现是独立复核证据，不构成产物原先已经核验的证据。
