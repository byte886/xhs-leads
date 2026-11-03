import { defineConfig } from 'vitest/config'

// R-6.36d：vitest 配置。coverage 用 v8 + LCOV（crapper TS 映射读 LCOV）。
export default defineConfig({
  test: {
    environment: 'jsdom',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.ts'],
      exclude: ['src/main.ts', '**/*.d.ts', 'src/**/*.test.ts'],
    },
  },
})
