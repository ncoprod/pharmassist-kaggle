import { Fragment, type ReactNode } from 'react'

type MarkdownArticleProps = {
  markdown?: string
  className?: string
}

type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'ul'; items: string[] }
  | { type: 'ol'; items: string[] }
  | { type: 'paragraph'; text: string }

function safeHref(raw: string): string {
  const trimmed = raw.trim()
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) return trimmed
  return '#'
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\[([^\]]+)\]\(([^)]+)\)/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = re.exec(text)) != null) {
    if (match.index > lastIndex) {
      out.push(text.slice(lastIndex, match.index))
    }

    const label = match[1]
    const url = safeHref(match[2])
    out.push(
      <a key={`${keyPrefix}-a-${match.index}`} href={url} target="_blank" rel="noreferrer">
        {label}
      </a>,
    )

    lastIndex = re.lastIndex
  }

  if (lastIndex < text.length) {
    out.push(text.slice(lastIndex))
  }

  if (out.length === 0) out.push(text)
  return out
}

function parseMarkdown(markdown: string): Block[] {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i].trim()
    if (!line) {
      i += 1
      continue
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(line)
    if (heading) {
      blocks.push({
        type: 'heading',
        level: heading[1].length,
        text: heading[2],
      })
      i += 1
      continue
    }

    const ul = /^[-*]\s+(.+)$/.exec(line)
    if (ul) {
      const items: string[] = []
      while (i < lines.length) {
        const current = lines[i].trim()
        const m = /^[-*]\s+(.+)$/.exec(current)
        if (!m) break
        items.push(m[1])
        i += 1
      }
      blocks.push({ type: 'ul', items })
      continue
    }

    const ol = /^(\d+)\.\s+(.+)$/.exec(line)
    if (ol) {
      const items: string[] = []
      while (i < lines.length) {
        const current = lines[i].trim()
        const m = /^(\d+)\.\s+(.+)$/.exec(current)
        if (!m) break
        items.push(m[2])
        i += 1
      }
      blocks.push({ type: 'ol', items })
      continue
    }

    const paragraph: string[] = [line]
    i += 1
    while (i < lines.length) {
      const current = lines[i].trim()
      if (!current) break
      if (/^(#{1,4})\s+/.test(current)) break
      if (/^[-*]\s+/.test(current)) break
      if (/^(\d+)\.\s+/.test(current)) break
      paragraph.push(current)
      i += 1
    }
    blocks.push({ type: 'paragraph', text: paragraph.join(' ') })
  }

  return blocks
}

export function MarkdownArticle({ markdown, className }: MarkdownArticleProps) {
  const content = (markdown ?? '').trim()
  if (!content) return <div className={className ?? ''}>—</div>

  const blocks = parseMarkdown(content)

  return (
    <article className={className}>
      {blocks.map((block, idx) => {
        if (block.type === 'heading') {
          if (block.level === 1) {
            return <h1 key={`h1-${idx}`}>{block.text}</h1>
          }
          if (block.level === 2) {
            return <h2 key={`h2-${idx}`}>{block.text}</h2>
          }
          return <h3 key={`h3-${idx}`}>{block.text}</h3>
        }

        if (block.type === 'ul') {
          return (
            <ul key={`ul-${idx}`}>
              {block.items.map((item, itemIdx) => (
                <li key={`ul-${idx}-${itemIdx}`}>{renderInline(item, `ul-${idx}-${itemIdx}`)}</li>
              ))}
            </ul>
          )
        }

        if (block.type === 'ol') {
          return (
            <ol key={`ol-${idx}`}>
              {block.items.map((item, itemIdx) => (
                <li key={`ol-${idx}-${itemIdx}`}>{renderInline(item, `ol-${idx}-${itemIdx}`)}</li>
              ))}
            </ol>
          )
        }

        return (
          <Fragment key={`p-${idx}`}>
            <p>{renderInline(block.text, `p-${idx}`)}</p>
          </Fragment>
        )
      })}
    </article>
  )
}
