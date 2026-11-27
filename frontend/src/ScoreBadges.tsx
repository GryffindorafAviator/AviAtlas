import React from 'react';
import type { Source } from './api';

/** Per-strategy ranks and scores for one result. Scores of different strategies are not comparable. */
export function ScoreBadges({ source }: { source: Source }) {
  const scores = source.scores ?? {}, ranks = source.ranks ?? {};
  const badges: [string, string, string][] = [];
  if ('dense' in scores) badges.push(['dense', `语义 #${ranks.dense ?? '—'}`, scores.dense.toFixed(3)]);
  if ('bm25' in scores) badges.push(['bm25', `关键词 #${ranks.bm25 ?? '—'}`, scores.bm25.toFixed(2)]);
  if ('rrf' in scores) badges.push(['rrf', '融合', scores.rrf.toFixed(4)]);
  if (!badges.length) return null;
  return <div className="score-badges">
    {badges.map(([key, label, value]) => <span key={key} className={`score-badge score-${key}`}>{label} · {value}</span>)}
    {!!source.matched_terms?.length && <span className="matched-terms">命中词：{source.matched_terms.join('、')}</span>}
  </div>;
}
