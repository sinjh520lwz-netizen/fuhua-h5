#!/bin/bash
# infinite_learning.sh

round=1
base_topics=("量化策略" "因子工程" "回测系统" "组合优化" "风险管理")

while true; do
  echo "========== 第 ${round} 轮学习开始 =========="
  
  for topic in "${base_topics[@]}"; do
    prompt="深度研究${topic}，难度等级：${round}级（逐级加深）。
    要求：
    - 比上一轮的理解更深一层，数学推导更严谨
    - 新增至少3个之前没研究过的子方向
    - 用更复杂的数学工具（第${round}轮对应${round}阶数学复杂度）
    - 输出不少于$(($round * 2))万字
    - 必须包含原创性思考，不能只是复述已有知识
    - 每一部分都要自我质疑、自我反驳、再自我修正"
    
    hermes chat -q "$prompt" \
      --provider mimo -m mimo-v2.5-pro \
      --max-turns $((100 + round * 50)) --accept-hooks \
      > "output/round${round}_${topic}.md" 2>&1
  done
  
  echo "========== 第 ${round} 轮完成，休息30分钟 =========="
  sleep 1800
  round=$((round + 1))
done
