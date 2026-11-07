import { describe, expect, it } from 'vitest'
import { filterNotes, matchNote, splitTerms } from './noteFilter'
import type { Note } from '../types'

function note(partial: Partial<Note>): Note {
  return {
    feed_id: '6abcce8400000000180122a3',
    keyword: '翡翠',
    title: '',
    note_type: 'normal',
    user_id: '6a66f60e0000000013020401',
    nickname: null,
    avatar: null,
    cover_url: null,
    xsec_token: null,
    desc: null,
    ip_location: null,
    liked_count: 0,
    collected_count: 0,
    comment_count: 0,
    shared_count: 0,
    fans_count: 0,
    publish_time: null,
    score: 0,
    tag: null,
    fan_ratio: null,
    fetched_at: null,
    ...partial
  }
}

const N1 = note({ title: '繁花翡翠手链，每条都是独一无二的存在！', nickname: '臻翠繁花' })
const N2 = note({ title: '500的翡翠手串到底长啥样？', nickname: '就叫四哥' })
const N3 = note({ title: '满绿手串价格', nickname: '老许翡翠避坑指南' })

describe('splitTerms', () => {
  it('空串与纯空格不产生词', () => {
    expect(splitTerms('')).toEqual([])
    expect(splitTerms('   ')).toEqual([])
    expect(splitTerms(null)).toEqual([])
  })

  it('按空白拆词并转小写', () => {
    expect(splitTerms('  Jade  手串 ')).toEqual(['jade', '手串'])
  })
})

describe('matchNote', () => {
  it('没有词时命中任何笔记', () => {
    expect(matchNote(N1, [])).toBe(true)
  })

  it('命中标题子串（不要求从头匹配）', () => {
    expect(matchNote(N1, ['翡翠'])).toBe(true)
    expect(matchNote(N1, ['独一无二'])).toBe(true)
  })

  it('命中作者昵称', () => {
    expect(matchNote(N3, ['老许'])).toBe(true)
  })

  it('标题里没有、作者名里也没有则不命中', () => {
    expect(matchNote(N2, ['臻翠'])).toBe(false)
  })
})

describe('filterNotes', () => {
  it('空查询原样返回同一数组', () => {
    const all = [N1, N2, N3]
    expect(filterNotes(all, '')).toBe(all)
  })

  it('标题关键词筛出对应笔记', () => {
    expect(filterNotes([N1, N2, N3], '手串').map(n => n.nickname)).toEqual(['就叫四哥', '老许翡翠避坑指南'])
  })

  it('多词按 AND：一个词命中标题、另一个命中作者也能筛出', () => {
    expect(filterNotes([N1, N2, N3], '翡翠 四哥').map(n => n.nickname)).toEqual(['就叫四哥'])
  })

  it('多词中任一词不命中即排除', () => {
    expect(filterNotes([N1, N2, N3], '翡翠 不存在')).toEqual([])
  })

  it('不再按笔记ID/作者ID 筛选（这两个 ID 用户拿不到）', () => {
    expect(filterNotes([N1, N2, N3], '6abcce8400000000180122a3')).toEqual([])
    expect(filterNotes([N1, N2, N3], '6a66f60e0000000013020401')).toEqual([])
  })

  it('英文忽略大小写', () => {
    const en = note({ title: 'Jade Bracelet', nickname: 'Coco' })
    expect(filterNotes([en], 'jade').length).toBe(1)
    expect(filterNotes([en], 'COCO').length).toBe(1)
  })
})
