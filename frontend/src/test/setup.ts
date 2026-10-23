import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { resetQueryCache } from '../lib/queryCache'

// jsdom ships no ResizeObserver, and recharts' ResponsiveContainer constructs one
// in a mount effect. The throw escapes React, which unmounts the whole tree, so
// every assertion in a page that renders a chart fails with an empty body.
globalThis.ResizeObserver = class {
	observe(): void {}
	unobserve(): void {}
	disconnect(): void {}
}

afterEach(() => {
	cleanup()
	// Pages fetch through the shared query cache, so a cache left over from the
	// previous test would short-circuit the next test's mocked fetch.
	resetQueryCache()
})
