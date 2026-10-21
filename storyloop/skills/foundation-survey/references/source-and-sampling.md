# 来源、抽检与人工补证（质量约定 1）

技能 1.1.0；与八份文档、原 JSON 和 BASE 兼容。以下是技能/人工质量约定，不是引擎拒绝条件。`SURVEYED` 只表示文件安装及提案完成，不证明知识准确或 BASE 已生效。

## 事实记录

每个可复核结论一个局部 ID，例如 `architecture:F001`。一般机制、权限、依赖、扩展、数据写入和测试结论都属于事实；标题、纯导航不编号。不要把多个互不相干结论挤到同一 ID。

| 字段 | 必须记录什么 |
|---|---|
| ID / 结论 | 文档内稳定 ID；能单独复核的结论与适用范围 |
| 来源状态 | **引用已确认 / 仅凭名称推测 / 文档声称**，选择一个 |
| 核验结果 | **已核对 / 推断 / 待确认 / 冲突**；节覆盖可另记“不适用”并说明探查范围和理由 |
| 来源 | 仓库相对路径 + 符号或行号；来源种类为代码、活规格、构建、测试定义、运行记录、历史文档 |
| 观测提交 | 实际阅读版本的完整 SHA；脏状态与影响范围也要记录 |
| 限制 | 已看/没看什么；不能据此执行或推断什么；运行证据另记命令、结果及来源 |

“引用已确认”表示实际打开直接证据；路径存在本身不是证明。“文档声称 / 已核对”只表示核对原文，不能变成运行验证。仅从名称推测的必须说明缺少哪些直接证据。没有任何来源的空白写待确认，不伪造一种来源。

示例：`architecture:F001 | lookup 实际使用精确相等，活规格要求前缀，尚不一致 | 引用已确认 | 冲突 | app/catalog.py:lookup；openspec/specs/search/spec.md | <本次实际观测完整SHA> | 已读代码/规格，未执行测试`。示例中的 SHA 必须在真实产物替换，不能照抄占位。

代码说明“现在如何”，活规格说明“应当如何”；不同则保留两侧来源并标冲突。README 的历史说法、未执行的测试、未打开的外链都不能当作已验证实现。不可访问的来源保留待确认和已探查范围；不能因 Git 忽略目录就说文件不存在。

README 汇总推断、待确认、冲突和未覆盖节，链接回事实 ID。为空时写“本次已登记范围内无”，不承诺全仓无未知。旧库无元数据时写“旧格式，未按约定 1 核验”；刷新时只在重新核对后赋已核对，不自动继承。

## 冻结候选与选样规则 v1

1. 冻结七份必需正文及附加知识 Markdown 的全部事实 ID（含已核对、推断、待确认、冲突）。README 的索引/汇总和 errata 不重复入池；README 基线等元数据与勘误冲突另做定向核对。不适用节另做覆盖检查。不得为减轻核验而删去难查事实。
2. 每项是 `(文档路径, 事实 ID)`，必须唯一；路径与 ID 不能含 Tab/CR/LF。用仓库相对的 POSIX 路径，按路径、ID 的 UTF-8 字节排序。每项编码为 `路径 + Tab + ID + LF`，连接后算 SHA-256，保留完整清单及摘要。
3. 清单冻结后，复核者用只读工具生成一次种子：16 个随机字节，记 32 位小写 hex。同轮不换种子。按 `SHA-256(seed + LF + 路径 + LF + ID)` 的 UTF-8 文本 hash 升序排序，同 hash 按路径/ID 排序。
4. N=入池事实数，D=至少一条入池事实的正文数。先取每个正文 hash 第一条，再从全局剩余排序补足 `min(N,max(7,D,ceil(N×10%)))`，不重复；结果以全局 hash 顺序列出。N=0 写无可抽样事实，不能记通过。

下面一个完整标准库示例只处理显式候选元数据，不读取来源、不扫描仓库、不调用网络或执行来源脚本。将代码原样放入临时 `.py`，用 Python 运行；函数输入是 pair 列表和已记录种子。命令行示例输入 JSON：`{"candidates":[["openspec/foundation/api.md","api:F001"]],"seed":"0123456789abcdef0123456789abcdef"}`，从 stdin 读入。真实种子用 `secrets.token_hex(16)` 单独生成一次并记录；固定示例种子仅用于重放测试。

```python
import hashlib
import json
from pathlib import PurePosixPath
import re
import sys


def _fact_pair(pair):
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValueError('Each candidate must be [document, fact_id]')
    if any(not isinstance(value, str) or not value.strip()
           or any(mark in value for mark in ('\t', '\r', '\n')) for value in pair):
        raise ValueError('Candidate fields must be nonempty and contain no Tab/CR/LF')
    document, fact_id = pair
    path = PurePosixPath(document)
    if (path.is_absolute() or '..' in path.parts or ':' in document or '\\' in document
            or str(path) != document or not document.endswith('.md')):
        raise ValueError('Use canonical repository-relative Markdown paths')
    try:
        document.encode('utf-8')
        fact_id.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError('Candidate fields must encode as UTF-8') from error
    return document, fact_id


def sample_facts(candidates, seed):
    if not isinstance(seed, str) or re.fullmatch('[0-9a-f]{32}', seed) is None:
        raise ValueError('Record one 16-byte lowercase hexadecimal seed')
    pairs = [_fact_pair(pair) for pair in candidates]
    pairs = [pair for pair in pairs if PurePosixPath(pair[0]).name.casefold()
             not in {'readme.md', 'errata.md'}]
    if len(set(pairs)) != len(pairs):
        raise ValueError('Duplicate fact candidate')
    ordered = sorted(pairs, key=lambda pair: (pair[0].encode('utf-8'), pair[1].encode('utf-8')))
    encoded = ''.join(document + '\t' + fact_id + '\n' for document, fact_id in ordered)
    digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    ranking = sorted(ordered, key=lambda pair: (
        hashlib.sha256((seed + '\n' + pair[0] + '\n' + pair[1]).encode('utf-8')).hexdigest(),
        pair[0].encode('utf-8'), pair[1].encode('utf-8')))
    first = {}
    for pair in ranking:
        first.setdefault(pair[0], pair)
    n, d = len(ordered), len(first)
    count = min(n, max(7, d, (n + 9) // 10))
    chosen = set(first.values())
    for pair in ranking:
        if len(chosen) >= count:
            break
        chosen.add(pair)
    return {'rule_version': 1, 'seed': seed, 'candidate_sha256': digest,
            'N': n, 'D': d, 'sample_size': count,
            'candidates': [list(pair) for pair in ordered],
            'samples': [list(pair) for pair in ranking if pair in chosen],
            'status': 'selected' if n else 'no_facts'}


if __name__ == '__main__':
    request = json.load(sys.stdin)
    print(json.dumps(sample_facts(request['candidates'], request['seed']), ensure_ascii=False))
```

只把程序输出当选样记录，不是核验结论。工具不可运行或不能可靠计算时写“抽检未完成”，把完整候选和原因交给复核者，不凭印象填一个 digest。旧库没有 ID 时先人工编制对应清单，再抽样，不自动改原文。

边界例：七正文各一事实→N=7/D=7/抽7；再加一份附加正文→8/8/抽8；七正文共100事实→100/7/抽10；不足七条全部取；空池不能通过。

## 重新核验与保留失败

选样后逐条重新打开非敏感来源，核对结论、范围、状态与版本。随机样本之外，定向检查本轮变更涉及的权限、行为开关、测试身份隔离、写入边界、BASE/观测 SHA 及勘误冲突。只核对命令定义不等于运行；未经授权不执行可能有副作用的命令。

结果分别为“相符 / 不符 / 无法核验”。已核对事实无法复核时降为待确认，说明原因；不符项修正并检查同节相关断言。推断/待确认本身不是错，写成确定事实或遗漏限制才是不符。保留原结论、失败样本、修订后结论和复核结果；新增事实单列补抽，不重新换种子消除失败。

README 的“来源与抽检记录”必须包含：观测 SHA、技能/约定版本、完整候选与摘要、种子/算法版本、N/D/样本数、样本 ID、逐条结果及依据、未核验原因、修订前后内容、自检或独立复核身份。只由同一代理选样并核验时写“自检”，未发生独立复核不能补写“已独立审查”。随机抽检不证明完整性，也不输出统计准确率。

新 survey 尚未受保护时可在候选文档修正；已有受 BASE 保护正文的错误走现有 errata：精确原文 `claim` + `document` + 修正 `correction` + 当前真实源码 `path:line`，由支持 errata 的既有角色捕获草案，再人工批准和 BASE。独立人工复核没有专用 CLI，保留发现、交现有流程处理；没有有效证据就记无法核验，不伪造位置。勘误未落地或 BASE 未通过只记“发现待处理”。

交付质量门：八文档覆盖检查、来源记录、可重放选样、逐条核验与修订轨迹完成。未确认项可保留，但 README 必须显式列出，不能把未确认命令、权限或数据边界当操作依据。到期复核仅为维护者约定，不增加后台行为。

## 未覆盖概念的人工双文本补证

1. `uncovered_concepts` 转出的 knowledge 草案只视为补证任务；默认 pending。优先落在职责匹配的现有八文档，附加 Markdown 仍兼容。
2. 核实真实来源、范围、事实 ID、状态、观测版本和冲突；发现相反结论走 errata，不追加第二个矛盾“事实”。
3. 同时审阅并补齐草案 `body.md`（经验正文）与 `draft.json` 的 `payload.landing.append`（知识库实际追加内容）。只改 body 不意味着 append 已补；人工检查未通过就不调用审批。不能冒称引擎强拒占位或自动同步正文。
4. 记录人工修订前后和原因，不称为模型自然产物。未补完保留 pending 或带理由拒绝。
5. 正式 `approve --draft` 后核对 lesson 和实际追加内容、BASE 提案精确写集；只有 `base-update` 成功后才依赖新增知识。若 README/其他文档需联动，独立 BASE 修订，不扩大原草案审批写集。
6. 下一次 survey 经 BASE 吸收已批准勘误，保留来源关系，不抹去有效勘误记录。没有新增 survey payload；人可以在既有 reason 提醒需补查内容，reason 不是来源证明。
