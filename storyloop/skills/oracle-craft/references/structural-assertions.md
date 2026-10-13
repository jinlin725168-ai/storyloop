# 结构断言（refactor 的判据）

结构断言在旧代码上必须失败（结构目标尚未达成），实现后通过。

- 判定到 import 语句或全限定名，不做裸文本匹配：避免同名类误报（两个包里都有 `BaseMapper`）和注释里的提及。
- 普查范围等于合同 `refactor.scope` 陈述的范围，不能在自选的子范围里宣布完成。
- dependency-swap 的默认断言是"再扫为零"：被移除目标的全限定名（含伴生库与传递依赖）在范围内的计数为 0，依赖清单里没有残留。
- 每条结构目标对应一个测试，测试 id 写进清单，`criteria` 指向对应的验收条件。

示例（Python 标准库实现的 grep 断言，退出码：0 达成、1 未达成）：

```python
from pathlib import Path
import re
import sys

PATTERN = re.compile(r"^\s*(from\s+legacy_http\b|import\s+legacy_http\b)", re.M)
hits = [str(p) for p in Path("modules/orders").rglob("*.py") if PATTERN.search(p.read_text(encoding="utf-8"))]
print(f"legacy_http imports: {len(hits)}", *hits, sep="\n")
sys.exit(0 if not hits else 1)
```
