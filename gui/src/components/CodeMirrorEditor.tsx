import { useEffect, useRef, useCallback } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, placeholder as cmPlaceholder } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { syntaxHighlighting, defaultHighlightStyle, indentOnInput, bracketMatching } from '@codemirror/language'
import { markdown, markdownLanguage } from '@codemirror/lang-markdown'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'

interface CodeMirrorEditorProps {
  value: string
  onChange: (value: string) => void
  language: 'markdown' | 'yaml'
  readOnly?: boolean
}

export function CodeMirrorEditor({ value, onChange, language, readOnly = false }: CodeMirrorEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange)

  onChangeRef.current = onChange

  const handleChange = useCallback((val: string) => {
    onChangeRef.current(val)
  }, [])

  useEffect(() => {
    if (!containerRef.current) return

    const langExtension = language === 'markdown'
      ? markdown({ base: markdownLanguage })
      : yaml()

    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        const newValue = update.state.doc.toString()
        handleChange(newValue)
      }
    })

    const state = EditorState.create({
      doc: value,
      extensions: [
        langExtension,
        oneDark,
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        history(),
        indentOnInput(),
        bracketMatching(),
        cmPlaceholder(language === 'markdown' ? '# 输入人设...' : '# 输入配置...'),
        updateListener,
        EditorView.lineWrapping,
        EditorView.editable.of(!readOnly),
        EditorState.readOnly.of(readOnly),
      ],
    })

    const view = new EditorView({
      state,
      parent: containerRef.current,
    })

    viewRef.current = view

    return () => {
      view.destroy()
      viewRef.current = null
    }
  }, [language, readOnly])

  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    if (value !== view.state.doc.toString()) {
      view.dispatch({
        changes: {
          from: 0,
          to: view.state.doc.length,
          insert: value,
        },
      })
    }
  }, [value])

  return (
    <div
      ref={containerRef}
      className="cm-editor-wrapper flex-1 overflow-auto bg-zinc-950 rounded border border-zinc-800"
    />
  )
}
