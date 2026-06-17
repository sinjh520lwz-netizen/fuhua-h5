#!/bin/bash
# practical_learning.sh - 实用量化学习

round=1
base_topics=("回测系统" "因子工程" "量化策略")

while true; do
  echo "========== 第 ${round} 轮学习开始 =========="
  
  for topic in "${base_topics[@]}"; do
    prompt="深度研究${topic}，难度等级：${round}级。
    要求：
    - 聚焦实战应用，不要纯理论
    - 用真实A股数据和代码示例
    - 每个方法都要说明：什么场景用、怎么用、注意事项
    - 输出不少于$(($round * 1))万字
    - 必须包含可直接运行的Python代码
    - 重点：能帮我提高选股准确率和回测效率"
    
    hermes chat -q "$prompt" \
      --provider mimo -m mimo-v2.5-pro \
      --max-turns $((80 + round * 30)) --accept-hooks \
      > "output/practical_r${round}_${topic}.md" 2>&1
  done
  
  echo "========== 第 ${round} 轮完成，休息15分钟 =========="
  sleep 900
  round=$((round + 1))
done
