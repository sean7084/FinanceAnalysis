import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { resetQueryCache } from '../lib/queryCache'

afterEach(() => {
	cleanup()
	// Pages fetch through the shared query cache, so a cache left over from the
	// previous test would short-circuit the next test's mocked fetch.
	resetQueryCache()
})
