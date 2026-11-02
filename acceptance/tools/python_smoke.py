"""python 接入草案：未初始化，需人工补全；经受信 launcher 运行。

构建/启动必须离线；仅同步清单中 source_files，不扫描目录或读取凭证。
业务依赖在独立子进程中加载。不要在此解释器中导入候选业务包或修改 sys.path。
每个不需要的阶段须在 onboarding.json 中填写 not_applicable 和 reason。
"""


def run_case(context, case):
    from storyloop.testing.smoke_harness.v1 import python_cases
    return python_cases(context.project, [case], cwd=context.scratch.mirror)


def run(context):
    from storyloop.testing.smoke_harness.v1 import run_hooks
    return run_hooks(context, run_case=run_case)


if __name__ == "__main__":
    import sys
    print("未初始化、需人工补全；请通过同版本 StoryLoop 的受信 launcher 运行。")
    sys.exit(2)
