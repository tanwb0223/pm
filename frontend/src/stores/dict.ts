import { create } from 'zustand'

/** 全局字典缓存：学院、项目等下拉数据 */
interface DictState {
  colleges: { id: number; name: string }[]
  majors: { id: number; name: string }[]
  loaded: boolean
  setColleges: (list: { id: number; name: string }[]) => void
  setMajors: (list: { id: number; name: string }[]) => void
  setLoaded: (v: boolean) => void
  collegeName: (id: number | null | undefined) => string
  majorName: (id: number | null | undefined) => string
}

export const useDictStore = create<DictState>((set, get) => ({
  colleges: [],
  majors: [],
  loaded: false,

  setColleges: (colleges) => set({ colleges }),
  setMajors: (majors) => set({ majors }),
  setLoaded: (loaded) => set({ loaded }),

  collegeName: (id) => {
    if (id === null || id === undefined) return '—'
    return get().colleges.find((c) => c.id === id)?.name ?? `#${id}`
  },

  majorName: (id) => {
    if (id === null || id === undefined) return '—'
    return get().majors.find((m) => m.id === id)?.name ?? `#${id}`
  },
}))
