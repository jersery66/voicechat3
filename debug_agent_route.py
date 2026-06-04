"""Debug script to test agent route_conversation_actions independently.

Usage: python debug_agent_route.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.agent_service import get_agent_service

agent = get_agent_service()

print(f"Agent model: {agent.model}")
print(f"Agent available: {agent.is_available()}")
print()

cases = [
    {
        "user_text": "对，我心情不好",
        "recent_history": "用户：没有。\n用户：也没有。\n用户：都不喜欢\nAI：你说都不喜欢，像是对很多事都提不起劲。",
        "current_round": 4,
    },
    {
        "user_text": "没有原因",
        "recent_history": "用户：对，我心情不好\nAI：这种心情不好像不是某件事一下子引起的。",
        "current_round": 5,
    },
    {
        "user_text": "两三个星期了",
        "recent_history": "用户：对，我心情不好\n用户：没有原因\nAI：这种状态持续多久了？",
        "current_round": 6,
    },
    {
        "user_text": "睡不好，很焦虑",
        "recent_history": "用户：最近心情不好\nAI：我在听，你慢慢说。",
        "current_round": 5,
    },
    {
        "user_text": "别问了，我就是想聊天",
        "recent_history": "AI：睡眠怎么样？\n用户：睡不好\nAI：入睡难还是容易醒？",
        "current_round": 5,
    },
]

for i, c in enumerate(cases, 1):
    print(f"{'='*60}")
    print(f"Case {i}: {c['user_text']}")
    print(f"Round: {c['current_round']}")
    try:
        result = agent.route_conversation_actions(
            user_text=c["user_text"],
            recent_history=c["recent_history"],
            current_round=c["current_round"],
            active_scale=None,
            collected_scales={},
            relaxation_done=False,
            timeout=15.0,
        )
        print(f"Result: {result}")
        print(f"  scale_action={result.get('scale_action')}")
        print(f"  scale={result.get('scale')} item={result.get('item')}")
        print(f"  confidence={result.get('confidence')}")
        print(f"  recommend_relaxation={result.get('recommend_relaxation')}")
        print(f"  reason={result.get('reason')}")
    except Exception as e:
        print(f"ERROR: {e}")
    print()
