import { useMemo } from 'react'
import type { SeniorityLevel } from '@/api/types/enums'
import type { SetupAgentSummary } from '@/api/types/setup'
import { cn } from '@/lib/utils'
import { seniorityRank } from '@/utils/agents'

export interface MiniOrgChartProps {
  agents: readonly SetupAgentSummary[]
  className?: string
}

interface DeptNode {
  name: string
  agents: SetupAgentSummary[]
  /** Agent with the highest-ranked level in the department. */
  headAgent: SetupAgentSummary | null
}

/** Team size threshold for compact layout. */
const SMALL_TEAM_THRESHOLD = 5
const LARGE_AVATAR_RADIUS = 16
const SMALL_AVATAR_RADIUS = 14
const LARGE_NODE_WIDTH = 140
const SMALL_NODE_WIDTH = 120
const AGENT_SPACING_GAP = 10
/** Root company node radius. */
const ROOT_RADIUS = 10
/**
 * In-chart text sizes as design tokens. Per `web/CLAUDE.md`,
 * chart `fontSize` must use `var(--so-text-*)` tokens so the chart
 * scales with the typography density axis.  Modern browsers resolve
 * CSS variables inside SVG presentation attributes.
 */
const FONT_SIZE_AGENT = 'var(--so-text-micro)'
const FONT_SIZE_DEPT_LARGE = 'var(--so-text-compact)'
const FONT_SIZE_DEPT_SMALL = 'var(--so-text-micro)'
/** Bottom padding below agent row in the SVG viewport. */
const SVG_BOTTOM_PADDING = 20
/** Horizontal padding on each side of the chart. */
const SVG_HORIZONTAL_PADDING = 20

/**
 * Levels that get emphasized styling (department leaders, executives).
 * Seniority *ordering* lives in ``@/utils/agents`` (`SENIORITY_RANK`
 * / `seniorityRank`) so this file doesn't re-derive the same ladder.
 */
const LEADER_LEVELS: ReadonlySet<SeniorityLevel> = new Set([
  'c_suite', 'vp', 'director', 'principal', 'lead',
])

function getInitials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] ?? '')
    .join('')
    .toUpperCase()
}

/**
 * Format a snake_case department name as a Title-Case label.
 *
 * Example: ``quality_assurance`` → ``Quality Assurance``.
 */
function formatDeptName(snake: string): string {
  return snake
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

function pickHead(agents: readonly SetupAgentSummary[]): SetupAgentSummary | null {
  if (agents.length === 0) return null
  let head = agents[0]!
  for (const agent of agents) {
    if (seniorityRank(agent.level ?? null) > seniorityRank(head.level ?? null)) head = agent
  }
  return head
}

interface AgentNodeProps {
  agent: SetupAgentSummary
  agentX: number
  agentY: number
  deptX: number
  deptY: number
  radius: number
  deptHalfHeight: number
  isHead: boolean
}

function AgentNode({
  agent, agentX, agentY, deptX, deptY, radius, deptHalfHeight, isHead,
}: AgentNodeProps) {
  const isLeader = agent.level != null && LEADER_LEVELS.has(agent.level)
  const effectiveRadius = isHead ? radius + 2 : radius
  const strokeClass = isHead
    ? 'stroke-accent'
    : isLeader
      ? 'stroke-accent/70'
      : 'stroke-accent/30'
  const fillClass = isHead ? 'fill-accent/15' : 'fill-card'
  const titleSuffix = agent.level ? ` · ${agent.level.replace('_', '-')}` : ''
  return (
    <g>
      <line
        x1={deptX}
        y1={deptY + deptHalfHeight}
        x2={agentX}
        y2={agentY - effectiveRadius}
        className={cn('stroke-border', isHead && 'stroke-accent/50')}
        strokeWidth="var(--so-stroke-hairline)"
      />
      <circle
        cx={agentX}
        cy={agentY}
        r={effectiveRadius}
        className={cn(fillClass, strokeClass)}
        strokeWidth={
          isHead || isLeader
            ? 'var(--so-stroke-thin)'
            : 'var(--so-stroke-hairline)'
        }
      >
        <title>{`${agent.name} -- ${agent.role}${titleSuffix}${isHead ? ' (head)' : ''}`}</title>
      </circle>
      <text
        x={agentX}
        y={agentY + 3}
        textAnchor="middle"
        className={cn('fill-foreground', isHead ? 'font-semibold' : 'font-medium')}
        fontSize={FONT_SIZE_AGENT}
      >
        {getInitials(agent.name)}
      </text>
    </g>
  )
}

interface DepartmentGroupProps {
  pos: { x: number; y: number; dept: DeptNode }
  nodeWidth: number
  nodeHeight: number
  avatarRadius: number
  vGap: number
  isSmallTeam: boolean
}

function DepartmentGroup({
  pos, nodeWidth, nodeHeight, avatarRadius, vGap, isSmallTeam,
}: DepartmentGroupProps) {
  const label = formatDeptName(pos.dept.name)
  return (
    <g>
      <rect
        x={pos.x - nodeWidth / 2}
        y={pos.y - nodeHeight / 2}
        width={nodeWidth}
        height={nodeHeight}
        rx={6}
        className="fill-surface stroke-border"
        strokeWidth="var(--so-stroke-hairline)"
      />
      <text
        x={pos.x}
        y={pos.y + 4}
        textAnchor="middle"
        className="fill-foreground"
        fontSize={isSmallTeam ? FONT_SIZE_DEPT_LARGE : FONT_SIZE_DEPT_SMALL}
      >
        {label}
      </text>

      {pos.dept.agents.map((agent, agentIdx) => {
        const agentSpacing = avatarRadius * 2 + AGENT_SPACING_GAP
        const centerOffset = agentIdx - (pos.dept.agents.length - 1) / 2
        const agentX = pos.x + centerOffset * agentSpacing
        const agentY = pos.y + vGap
        // Object identity: ``headAgent`` is picked from the same
        // ``agents`` array via ``pickHead``, so reference equality is
        // safe.  Matching by name would mis-mark every agent sharing
        // a name with the head (setup templates can repeat names).
        const isHead = pos.dept.headAgent === agent
        return (
          <AgentNode
            // eslint-disable-next-line @eslint-react/no-array-index-key -- setup agents can share names; index as tiebreaker
            key={`${agent.name}-${agentIdx}`}
            agent={agent}
            agentX={agentX}
            agentY={agentY}
            deptX={pos.x}
            deptY={pos.y}
            radius={avatarRadius}
            deptHalfHeight={nodeHeight / 2}
            isHead={isHead}
          />
        )
      })}
    </g>
  )
}

export function MiniOrgChart({ agents, className }: MiniOrgChartProps) {
  const departments = useMemo<DeptNode[]>(() => {
    const deptMap = new Map<string, SetupAgentSummary[]>()
    for (const agent of agents) {
      const dept = agent.department || 'unassigned'
      const existing = deptMap.get(dept)
      if (existing) {
        existing.push(agent)
      } else {
        deptMap.set(dept, [agent])
      }
    }
    return [...deptMap.entries()].map(([name, deptAgents]) => ({
      name,
      agents: deptAgents,
      headAgent: pickHead(deptAgents),
    }))
  }, [agents])

  if (agents.length === 0) return null

  const isSmallTeam = agents.length <= SMALL_TEAM_THRESHOLD
  const avatarRadius = isSmallTeam ? LARGE_AVATAR_RADIUS : SMALL_AVATAR_RADIUS
  // Budget head bump (+2) + stroke width so the head circle can't
  // visually overflow the computed row height.
  const agentRowHalfHeight = avatarRadius + 3
  const nodeWidth = isSmallTeam ? LARGE_NODE_WIDTH : SMALL_NODE_WIDTH
  const nodeHeight = 32
  const hGap = isSmallTeam ? 40 : 24
  const vGap = isSmallTeam ? 56 : 48

  const deptWidths = departments.map((d) =>
    Math.max(nodeWidth, d.agents.length * (avatarRadius * 2 + AGENT_SPACING_GAP)),
  )
  const totalWidth = deptWidths.reduce((sum, w) => sum + w + hGap, 0) - hGap
  const svgWidth = Math.max(totalWidth + SVG_HORIZONTAL_PADDING * 2, 300)

  // Vertical layout (single row per department; agents laid horizontally
  // below the dept box -- NOT stacked). Positions:
  //   root center      y = ROOT_RADIUS + 6
  //   dept center      y = vGap
  //   agent center     y = vGap + vGap = 2 * vGap
  //   agent bottom     y = 2 * vGap + agentRowHalfHeight
  const rootY = ROOT_RADIUS + 6
  const deptY = vGap
  const agentY = 2 * vGap
  const svgHeight = agentY + agentRowHalfHeight + SVG_BOTTOM_PADDING

  let xOffset = (svgWidth - totalWidth) / 2
  const deptPositions = departments.map((dept, i) => {
    const width = deptWidths[i]!
    const x = xOffset + width / 2
    xOffset += width + hGap
    return { x, y: deptY, dept }
  })

  const rootX = svgWidth / 2

  return (
    <div className={cn('overflow-x-auto rounded-lg border border-border bg-card p-card', className)}>
      <svg
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        width="100%"
        // Constrain max width so the chart doesn't scale up absurdly on
        // very wide screens -- we want it legible, not gigantic.
        style={{ maxWidth: svgWidth, display: 'block', margin: '0 auto' }}
        role="img"
        aria-label="Organization chart"
      >
        <circle cx={rootX} cy={rootY} r={ROOT_RADIUS} className="fill-accent" />

        {deptPositions.map((pos) => (
          <line
            key={`root-${pos.dept.name}`}
            x1={rootX}
            y1={rootY + ROOT_RADIUS}
            x2={pos.x}
            y2={pos.y - nodeHeight / 2}
            className="stroke-border"
            strokeWidth="var(--so-stroke-hairline)"
          />
        ))}

        {deptPositions.map((pos) => (
          <DepartmentGroup
            key={pos.dept.name}
            pos={pos}
            nodeWidth={nodeWidth}
            nodeHeight={nodeHeight}
            avatarRadius={avatarRadius}
            vGap={vGap}
            isSmallTeam={isSmallTeam}
          />
        ))}
      </svg>
    </div>
  )
}
