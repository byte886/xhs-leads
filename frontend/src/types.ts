export type Stage = 'input' | 'search' | 'filter' | 'pick'

export interface Note {
  feed_id: string
  keyword: string | null
  title: string
  note_type: string
  user_id: string | null
  nickname: string | null
  avatar: string | null
  cover_url: string | null
  xsec_token: string | null
  /** 原作品链接（explore 页），供「打开原作品」跳转 */
  origin?: string
  desc: string | null
  ip_location: string | null
  liked_count: number | null
  collected_count: number | null
  comment_count: number | null
  shared_count: number | null
  fans_count: number | null
  publish_time: string | null
  score: number | null
  tag: string | null
  fan_ratio: number | null
  fetched_at: string | null
  /** 前端临时字段：是否被选中 */
  selected?: boolean
  /** 前端临时字段：展示用本地图片 */
  local_images?: string[]
  /** 后端返回：本地代理封面（/app/images/...），空则用 cover_url */
  local_cover?: string
  /** 评分明细（4 维度得分），供分数可解释展示 */
  score_info?: {
    score: number
    tag: string
    fan_ratio: number
    dimensions: {
      like_fan_ratio: number
      low_fan: number
      interaction_health: number
      freshness: number
    }
  }
}

export interface SearchParams {
  keyword: string
  limit: number
  sort: string
  noteType: string
  timeRange: string
  scope: string
  distance: string
  interval: number
  /** 快速档：后端同页连续导航补全，更快但节奏更密（默认关，拟人优先） */
  fast?: boolean
}

export interface SearchResult {
  added: number
  total: number
  top: Array<{ feed_id: string; title: string; score: number }>
  note?: string
}

/** 作者价值聚合（P2）：当前采集集按作者统计，识别值得模仿的博主 */
export interface Author {
  user_id: string
  nickname: string
  avatar: string
  fans_count: number
  note_count: number
  hot_count: number
  low_count: number
  avg_score: number
  max_score: number
  total_liked: number
  last_publish: number | null
}

/** 客户库（O1-3）：已采集的笔记互动用户，按互动足迹聚合 + 意向分级 */
export interface ClientUser {
  uid: string
  nickname: string
  comment_count: number
  note_count: number
  sample_comment: string
  profile: string
  created_at: string
  /** O1-3 意向分级：high/upper/middle/low */
  intent?: string
  matched_word?: string
  ip_location?: string
  comment_time?: string
}

/** O1-4 客户库聚合：线索总数/意向分布/属地TOP/近7天/留联系方式 */
export interface ClientsAgg {
  total: number
  intent_dist: { intent: string; label: string; count: number }[]
  ip_top: { ip: string; count: number }[]
  week: { date: string; count: number }[]
  contact: number
}

export interface ClonePayload {
  feed_id: string
  title: string
  desc: string
  images: string[]
  detail_ok: boolean
  /** 本地图片（/app/images/...），优先于外链 images */
  images_local?: string[]
  /** 视频克隆：原视频本地路径（/videos/...），空=图文笔记 */
  video_file?: string
  /** 原视频远端地址（仅展示） */
  video_url?: string
}

export interface LoginStatus {
  logged_in: boolean
  nickname?: string
  [k: string]: unknown
}

export interface LogItem {
  [k: string]: unknown
}

export interface LicenseStatus {
  mode: 'free' | 'activation' | 'server'
  schema_version: number
  features: string[]
  machine_fingerprint: string
  has_signature: boolean
  has_activation: boolean
  activated_at: string | null
}
