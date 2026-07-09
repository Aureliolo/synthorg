import { isLocalUrl } from '@/utils/provider-locality'

describe('isLocalUrl', () => {
  it.each([
    'http://localhost:11434',
    'http://127.0.0.1:11434',
    'http://host.docker.internal:11434',
    'http://192.168.1.50:1234/v1',
    'http://10.0.0.5:8000/v1',
    'http://172.16.4.2:11434',
    'http://169.254.1.1:11434',
    'http://[::1]:11434',
  ])('treats %s as local', (url) => {
    expect(isLocalUrl(url)).toBe(true)
  })

  it.each([
    'https://api.openai.com/v1',
    'https://ollama.com/v1',
    'https://api.mammouth.ai/v1',
    'http://8.8.8.8/v1',
    'http://172.32.0.1/v1',
  ])('treats %s as remote', (url) => {
    expect(isLocalUrl(url)).toBe(false)
  })

  it.each([null, undefined, '', 'not a url'])(
    'treats %s as remote (absent/unparseable)',
    (url) => {
      expect(isLocalUrl(url)).toBe(false)
    },
  )
})
