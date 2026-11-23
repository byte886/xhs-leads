<script setup lang="ts">
/**
 * 设置 › 采集设置：全局请求间隔 + 极速采集开关。
 *
 * 注：设置页左侧该分类当前置灰（on: false），本组件只在被打开时才会挂载。
 *     原模板里的 .switch/.track/.thumb 在全仓没有任何 CSS 定义（真实控件就是一个裸 checkbox），
 *     这里按原样保留，不另造一套样式。
 */
import { computed } from 'vue'
import { appState } from '../../store'

// 全局请求间隔：单值基准 + 自动窄随机抖动（Scrapy download_delay 同思路）。
// UI 如实显示随机区间，避免用户把随机误读为"多处不同值"。
const intervalBase = computed(() => (appState.params.interval / 1000).toFixed(1))
const intervalMax = computed(() => ((appState.params.interval + 800) / 1000).toFixed(1))
</script>

<template>
  <div>
    <div class="set-row">
      <div class="row-main">
        <span class="row-label">极速采集</span>
        <label class="switch">
          <input type="checkbox" v-model="appState.params.fast" />
          <span class="track"><span class="thumb" /></span>
        </label>
        <span class="cur-val">{{ appState.params.fast ? '已开启' : '已关闭' }}</span>
      </div>
      <p class="set-hint">
        开启后跳过拟人等待，采集快约一倍（20 条约 5 分钟 → 2 分钟）；节奏更密、被平台风控的概率略升。
        建议默认关闭，赶数据时再开。
      </p>
    </div>
    <div class="set-row">
      <div class="row-main">
        <span class="row-label">全局请求间隔</span>
        <input v-model.number="appState.params.interval" type="number" min="1000" max="10000" step="100" class="num-input" />
        <span class="unit">毫秒</span>
        <span class="cur-val">实际每次 {{ intervalBase }}–{{ intervalMax }} 秒（随机）</span>
      </div>
      <p class="set-hint">
        每次页面操作之间的间隔。安全范围 1–10 秒，默认 1 秒（真人浏览节奏）。
        调大 = 采集更慢更稳；下限 1 秒受系统保护（不能更快，防风控）。开启极速采集时自动跳过该等待。
      </p>
    </div>
  </div>
</template>
