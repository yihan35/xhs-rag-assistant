from rag.generator import SYSTEM_PROMPT


def test_analysis_prompt_includes_travel_timeline_requirements():
    assert "几天几晚时间线" in SYSTEM_PROMPT
    assert "### Day 1｜主题或区域" in SYSTEM_PROMPT
    assert "09:00｜地点｜做什么" in SYSTEM_PROMPT
    assert "已移除" in SYSTEM_PROMPT
    assert "替换为" in SYSTEM_PROMPT
    assert "基于上轮计划重新排" in SYSTEM_PROMPT


def test_analysis_prompt_includes_interview_frequency_requirements():
    assert "高频面试题排行" in SYSTEM_PROMPT
    assert "按出现频率或多篇笔记共同提及程度从高到低排序" in SYSTEM_PROMPT
    assert "频率判断" in SYSTEM_PROMPT
    assert "来源笔记" in SYSTEM_PROMPT
    assert "不要编造精确次数" in SYSTEM_PROMPT
    assert "多篇提到" in SYSTEM_PROMPT
