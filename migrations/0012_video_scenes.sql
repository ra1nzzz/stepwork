-- Migration: 0012_video_scenes.sql
-- Version:   0012
-- Upstream:  0011_command_metrics.sql
-- Purpose:   S2「打通：一条流水线端到端」的地基 —— 分幕事实表。
--
--            此前整条流水线里**没有「幕」这个概念**：脚本只是一整块文本，
--            配音只知道总时长，渲染只能拿「纯色背景 + 一行 drawtext」糊过去。
--            结果是「单幕重渲」做不到（改一句要重渲全片），「字幕与配音对齐」
--            没法验证（没有每幕的起止秒）。
--
--            本表把「幕」变成一等事实：文案产出后落库，配音回填 audio_uri +
--            实测时长，配图回填 image_uri，渲染按 start_sec/duration_sec 驱动
--            画面。S1 已证明「Playwright 逐帧 + ffmpeg 管道」能出片，缺的就是
--            这一层数据。
-- Spec:      ROADMAP §S2（依赖 S1）；字段对齐 COMPLETED §2 已验证的
--            scenes.json / timeline.json 契约（id / text / emotion / highlight）

CREATE TABLE IF NOT EXISTS video_scenes (
    id             TEXT PRIMARY KEY,
    -- 所属脚本版本（脚本 ContentVersion）；删版本即删其分幕
    version_id     TEXT NOT NULL REFERENCES content_versions(id) ON DELETE CASCADE,
    -- 幕序号，从 0 起。同一版本内唯一（见下方 unique index）
    seq            INTEGER NOT NULL,
    -- 该幕口播文本（唯一事实来源；字幕与配音都从它派生）
    text           TEXT NOT NULL DEFAULT '',
    -- TTS 情绪指令（StepFun 的 instruction 会盖过 speed，需 atempo 归一化）
    emotion        TEXT,
    -- 该幕需要标红/高亮的关键词子串。
    -- 硬约束（已固化为断言）：必须是 text 的子串，否则标红静默失效
    highlight      TEXT,
    -- 该幕配音音频（TTS 合成后回填）
    audio_uri      TEXT,
    -- 该幕配图（image provider 产出后回填）；A 版零素材风格为 NULL
    image_uri      TEXT,
    start_sec      REAL NOT NULL DEFAULT 0,
    -- 该幕**实测**音频时长（不是估值）：画面被音频时长驱动，
    -- 反过来就是 huashu-design 说的「失败模式 #1 = 带配音的 PPT」
    duration_sec   REAL NOT NULL DEFAULT 0,
    -- S1 遗留项：抽帧目检撞上切句瞬间会取到空字幕（淡入刚开始）。
    -- 渲染器本就知道每幕第一句的实际起始秒，落库后目检脚本直接读它前移取样，
    -- 不必再依赖页面暴露 __getSentBorn()（design.html 至今没暴露）。
    born_at_sec    REAL,
    created_at     TEXT NOT NULL
);

-- 同一版本内 seq 唯一：并发写幕不会互相覆盖成两条同序号记录
CREATE UNIQUE INDEX IF NOT EXISTS idx_video_scenes_version_seq
    ON video_scenes(version_id, seq);
CREATE INDEX IF NOT EXISTS idx_video_scenes_version
    ON video_scenes(version_id);
