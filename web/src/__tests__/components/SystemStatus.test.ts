import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SystemStatus from '@/components/dashboard/SystemStatus.vue'
import type { HealthStatus } from '@/api/types'

const mockHealth: HealthStatus = {
  status: 'ok',
  persistence: true,
  message_bus: true,
  uptime_seconds: 3600,
  version: '0.1.0',
}

describe('SystemStatus', () => {
  it('renders "System Status" heading', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: null, wsConnected: false },
    })
    expect(wrapper.text()).toContain('System Status')
  })

  it('shows "Unreachable" when health is null', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: null, wsConnected: false },
    })
    expect(wrapper.text()).toContain('Unreachable')
  })

  it('shows "ok" status when health is provided', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: mockHealth, wsConnected: true },
    })
    expect(wrapper.text()).toContain('ok')
  })

  it('shows "degraded" status when health status is degraded', () => {
    const degraded: HealthStatus = { ...mockHealth, status: 'degraded' }
    const wrapper = mount(SystemStatus, {
      props: { health: degraded, wsConnected: true },
    })
    expect(wrapper.text()).toContain('degraded')
  })

  it('shows WebSocket "Connected" when wsConnected is true', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: mockHealth, wsConnected: true },
    })
    expect(wrapper.text()).toContain('Connected')
  })

  it('shows WebSocket "Disconnected" when wsConnected is false', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: mockHealth, wsConnected: false },
    })
    expect(wrapper.text()).toContain('Disconnected')
  })

  it('shows uptime when health is present', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: mockHealth, wsConnected: true },
    })
    // 3600 seconds = 1h 0m
    expect(wrapper.text()).toContain('1h')
  })

  it('shows version when health is present', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: mockHealth, wsConnected: true },
    })
    expect(wrapper.text()).toContain('0.1.0')
  })

  it('does not show uptime or version when health is null', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: null, wsConnected: false },
    })
    expect(wrapper.text()).not.toContain('0.1.0')
    expect(wrapper.text()).not.toContain('Uptime')
  })

  it('shows persistence as "OK" when true', () => {
    const wrapper = mount(SystemStatus, {
      props: { health: mockHealth, wsConnected: true },
    })
    // Both persistence and message_bus should show OK
    const text = wrapper.text()
    expect(text).toContain('Persistence')
    expect(text).toContain('OK')
  })

  it('shows persistence as "Down" when false', () => {
    const down: HealthStatus = { ...mockHealth, persistence: false }
    const wrapper = mount(SystemStatus, {
      props: { health: down, wsConnected: true },
    })
    expect(wrapper.text()).toContain('Down')
  })

  it('shows message bus as "Down" when false', () => {
    const down: HealthStatus = { ...mockHealth, message_bus: false }
    const wrapper = mount(SystemStatus, {
      props: { health: down, wsConnected: true },
    })
    expect(wrapper.text()).toContain('Down')
  })
})
