# film-brief-skill

本分支的最新候选为 **r72（2026-09-21）**，已完成三模型真实验证。

- [r72安装与实测说明](candidates/r72/README.md)
- [r72完整Skill源码](candidates/r72/film-brief-cleaning/)
- [r72安装包](dist/film-brief-cleaning-r72.zip?raw=1)

在WorkBuddy中加载上面的`film-brief-cleaning`文件夹，然后提供本期原始Excel和剧名。默认执行入口、数量和字数规则都在该文件夹的`SKILL.md`中。

| 模型 | 完整用时 | 正/客/负组数 | 展示样本 |
|---|---:|---:|---:|
| GLM-5.3-Flash | 26分42秒 | 12/1/3 | 111 |
| DeepSeek-V4.1-Flash | 21分11秒 | 13/1/2 | 135 |
| Hy4 | 25分48秒 | 12/2/2 | 122 |

这三次结果来自同一期独立实跑；明确AI稿漏入为0，编辑均已应用。工作台供人工选材，媒体覆盖和个别语义边界仍需关注，详见实测说明。

历史候选：[r66](candidates/r66/README.md)。根目录执行文件属于旧版对照；安装r72请使用上面的候选目录或安装包。
