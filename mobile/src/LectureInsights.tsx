import { useEffect, useState } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';
import { ApiError, lectureInsights } from './api';
import { Button, ErrorMessage } from './components';
import { patchClass } from './storage';
import { colors, ui } from './theme';
import { LectureInsights, SavedClass } from './types';

export function InsightsContent({ data, passageLimit = 5 }: { data: LectureInsights; passageLimit?: number }) {
  const reliability = data.transcription;
  const available = reliability.status === 'available' || reliability.status === 'partial';
  const related = data.concepts.groups.filter(group => group.section_indices.length > 1).length;
  return <View style={{ gap: 20 }}>
    {data.mock && <View style={ui.notice}><Text style={ui.text}>Example notes only—not analysis of your recording.</Text></View>}
    <View style={ui.card}>
      <Text style={ui.eyebrow}>TRANSCRIPTION RELIABILITY</Text>
      <Text style={ui.heading}>Passages worth checking</Text>
      <Text style={[ui.eyebrow, { color: colors.warning }]}>Rule-based</Text>
      {available ? <>
        <Text style={ui.heading}>{`${reliability.flagged_passages} flagged passage${reliability.flagged_passages === 1 ? '' : 's'}`}</Text>
        <Text style={ui.text}>{reliability.affected_sections} affected note sections · {reliability.analyzed_passages} of {reliability.total_passages} passages analyzed</Text>
        {reliability.status === 'partial' && <Text style={ui.muted}>Partial analysis</Text>}
        {!reliability.flagged_passages && <Text style={ui.text}>No passages flagged.</Text>}
        {reliability.passages.slice(0, passageLimit).map((passage, index) => <View key={index}
          style={{ gap: 8, padding: 14, borderRadius: 12, backgroundColor: colors.warningBg }}>
          <Text style={ui.label}>{passage.headings.join(' · ') || 'Transcript passage'}</Text>
          <Text selectable style={ui.text}>{passage.text}</Text>
        </View>)}
      </> : <Text style={ui.text}>{reliability.status === 'mock'
        ? 'Unavailable for example notes.'
        : 'Transcription analysis unavailable.'}</Text>}
    </View>
    <View style={ui.card}>
      <Text style={ui.eyebrow}>CONCEPT CLUSTERING</Text>
      <Text style={ui.heading}>Connections across your lecture</Text>
      {related > 0 && <Text style={ui.muted}>{`${related} related group${related === 1 ? '' : 's'}`}</Text>}
      {!related && <Text style={ui.muted}>No related groups found.</Text>}
    </View>
    {data.concepts.groups.map((group, index) => <View key={index} style={ui.card}>
      <Text style={ui.eyebrow}>{group.section_indices.length > 1 ? `${group.headings.length} related sections` : 'Topic'}</Text>
      <View style={ui.row}>{group.terms.map(term => <Text key={term} style={[ui.label,
        { backgroundColor: colors.soft, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 6 }]}>{term}</Text>)}</View>
      {group.headings.map((heading, i) => <Text key={i} style={ui.text}>• {heading}</Text>)}
    </View>)}
  </View>;
}

export function LectureInsightsPanel({ item, onSaved }: { item: SavedClass; onSaved: () => Promise<void> }) {
  const [data, setData] = useState(item.insights);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [passageLimit, setPassageLimit] = useState(5);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (data?.version === 2) return;
    if (!item.remoteId || item.serverDeleted) {
      setError('Insights were not saved before the server copy was removed. Your study notes are still available.');
      return;
    }
    let active = true;
    setBusy(true); setError(undefined);
    void lectureInsights(item).then(async value => {
      if (!active) return;
      setData(value);
      try { await patchClass(item.id, { insights: value }); await onSaved(); }
      catch { if (active) setError('Insights are visible but could not be saved offline. Check phone storage.'); }
    }).catch((e: unknown) => {
      if (!active) return;
      setError(e instanceof ApiError && e.status === 410
        ? 'The server copy has expired. Your saved study notes are unaffected.'
        : e instanceof ApiError && e.status === 404
          ? 'Insights are unavailable. Check that the backend is updated and this server copy still exists.'
          : 'Could not load insights. Check your connection and try again; your notes are safe.');
    }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
    // A tab visit reads the saved snapshot once; refreshing never reruns Whisper or Gemma.
  }, [item.id, attempt]);

  return <View style={{ gap: 16 }}>
    {busy && <View style={ui.card}><ActivityIndicator /><Text style={ui.text}>Finding connections in your saved notes…</Text></View>}
    <ErrorMessage message={error} />
    {data && <InsightsContent data={data} passageLimit={passageLimit} />}
    {data && data.transcription.passages.length > passageLimit &&
      <Button label={`Show more flagged passages (${Math.min(passageLimit, data.transcription.passages.length)} of ${data.transcription.passages.length} shown)`}
        secondary onPress={() => setPassageLimit(value => value + 10)} />}
    {(!data || data.version !== 2) && !busy && !item.serverDeleted && item.remoteId &&
      <Button label={data ? 'Update insights' : 'Retry insights'} secondary onPress={() => setAttempt(value => value + 1)} />}
  </View>;
}
