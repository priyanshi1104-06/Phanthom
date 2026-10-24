// The Phantom side panel: Phantom talking about the file you're in. One sentence in Phantom's voice,
// then a card per reproduced failure: what fails, with which input, how Phantom found it and proved it,
// with buttons to jump to the line and to replay the input. The wording lives in findings.ts (tested);
// this file only renders it. The webview runs no remote code and only posts button clicks back.
import * as vscode from 'vscode';
import { PanelView } from './findings';

export type PanelAction = { type: 'reveal' | 'replay'; index: number } | { type: 'depth' | 'setUp' | 'checkIntent' };

export class PhantomPanel implements vscode.WebviewViewProvider, vscode.Disposable {
  static readonly id = 'phantom.panel';
  private view?: vscode.WebviewView;
  private last?: { view: PanelView; depth: string; models: string };
  private readonly listeners: vscode.Disposable[] = [];

  constructor(private readonly onAction: (action: PanelAction) => void) {}

  dispose(): void {
    this.listeners.forEach((d) => d.dispose());
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true, enableCommandUris: false };
    this.listeners.push(view.webview.onDidReceiveMessage((message: PanelAction) => {
      if (message && ['reveal', 'replay', 'depth', 'setUp', 'checkIntent'].includes(message.type)) {
        this.onAction(message);
      }
    }));
    this.render();
  }

  /** depth: "Auto"; models: the tiers in use, "Swift / Balanced" (or "engine only"). */
  show(view: PanelView, depth: string, models: string): void {
    this.last = { view, depth, models };
    this.render();
  }

  private render(): void {
    if (!this.view || !this.last) {
      return;
    }
    const { view, depth, models } = this.last;
    const nonce = Math.random().toString(36).slice(2);
    const cards = view.cards.map((card) => `
      <article class="card ${card.severity}">
        <div class="head"><span class="dot"></span>${esc(card.headline)} in <b>${esc(card.function)}</b>
          ${card.line ? `<span class="line">line ${card.line}</span>` : ''}</div>
        <code>${esc(card.call)}</code>
        <p>${esc(card.why)}</p>
        ${card.because ? `<p class="assume">Why: ${esc(card.because)}</p>` : ''}
        ${card.assumptions.length ? `<p class="assume">Assumes: ${card.assumptions.map(esc).join('; ')}</p>` : ''}
        <p class="how">${esc(card.how)}</p>
        <div class="buttons">
          <button data-type="reveal" data-index="${card.index}">Go to line</button>
          <button data-type="replay" data-index="${card.index}">▶ Replay</button>
        </div>
      </article>`).join('');
    this.view.webview.html = `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); padding: 0 10px; }
  .bar { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin: 10px 0; color: var(--vscode-descriptionForeground); }
  .bubble { background: var(--vscode-editorWidget-background); border: 1px solid var(--vscode-widget-border, transparent);
            border-radius: 10px; padding: 10px 12px; line-height: 1.45; }
  .who { font-weight: 600; margin-bottom: 4px; }
  .risk { margin-top: 6px; color: var(--vscode-descriptionForeground); }
  .card { border-left: 3px solid var(--vscode-editorError-foreground); padding: 8px 10px; margin: 10px 0;
          background: var(--vscode-editor-background); border-radius: 4px; }
  .card.warning { border-color: var(--vscode-editorWarning-foreground); }
  .card.info { border-color: var(--vscode-editorInfo-foreground); opacity: 0.85; }
  .dot { display: inline-block; width: 8px; height: 8px; margin-right: 7px; border-radius: 50%; vertical-align: 1px;
         background: #f14c4c; }
  .warning .dot { background: #f0a030; } .info .dot { background: var(--vscode-editorInfo-foreground); }
  .head { font-weight: 600; } .line { float: right; font-weight: normal; color: var(--vscode-descriptionForeground); }
  code { display: block; margin: 6px 0; font-family: var(--vscode-editor-font-family); }
  p { margin: 4px 0; } .how, .assume { color: var(--vscode-descriptionForeground); font-size: 0.92em; }
  .buttons { display: flex; gap: 6px; margin-top: 6px; }
  button { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
           border: none; padding: 3px 8px; border-radius: 3px; cursor: pointer; }
  button:hover { background: var(--vscode-button-secondaryHoverBackground); }
  a { color: var(--vscode-textLink-foreground); cursor: pointer; }
</style></head><body>
  <div class="bar"><span>${esc(view.title)}</span>
    <a data-type="depth" title="Choose the depth and the tier of each layer">${esc(depth)} · ${esc(models)}</a></div>
  <div class="bubble"><div class="who">Phantom</div>${esc(view.message)}
    ${view.risk ? `<div class="risk">This edit: ${esc(view.risk)}</div>` : ''}
    <div class="buttons">${/Set Up/.test(view.message) ? '<button data-type="setUp">Set Up</button>' : ''}
      <button data-type="checkIntent" title="Run the Analyst on this file now: wrong answers, not just crashes">Check intent</button></div></div>
  ${cards}
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-type]');
    if (target) {
      vscode.postMessage({ type: target.dataset.type, index: Number(target.dataset.index ?? -1) });
    }
  });
</script></body></html>`;
  }
}

function esc(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}
