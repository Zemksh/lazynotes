import { useEffect, useRef, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import { KeyboardAvoidingView, Platform, ScrollView, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { API_URL, health, Health } from '../src/api';
import { useClasses } from '../src/classes';
import { Button, Entrance, ErrorMessage, MockNotice } from '../src/components';
import { createClass, importPickedFile, readClass } from '../src/storage';
import { colors, ui } from '../src/theme';
import { Draft } from '../src/types';

const emptyDraft: Draft = { title: '', topics: '', syllabusText: '', audioUri: '', audioName: '', audioType: 'audio/mpeg' };

export default function NewClass() {
  const { retry } = useLocalSearchParams<{ retry?: string }>();
  const { submit, refresh } = useClasses();
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [server, setServer] = useState<Health>();
  const guard = useRef(false);
  useEffect(() => {
    void health().then(setServer).catch(() => {});
    if (retry) void readClass(retry).then(item => { if (item) setDraft(item.draft); }).catch(() => setError('The earlier class could not be opened.'));
  }, [retry]);
  const update = (patch: Partial<Draft>) => setDraft(current => ({ ...current, ...patch }));

  async function pick(kind: 'audio' | 'pdf') {
    try {
      // SDK 58's Android picker cache is outside the experience's allowed Expo Go
      // directory. Keep the granted provider URI and copy it into our own cache.
      const selection = await DocumentPicker.getDocumentAsync({ type: kind === 'audio' ? ['audio/*', 'video/mp4', 'application/ogg'] : 'application/pdf', copyToCacheDirectory: Platform.OS !== 'android' });
      const asset = selection.assets?.[0];
      if (selection.canceled || !asset) return;
      const limit = kind === 'audio' ? server?.max_audio_mb : server?.max_pdf_mb;
      if (limit && asset.size && asset.size > limit * 1024 * 1024) {
        setError(`Choose a ${kind === 'audio' ? 'recording' : 'PDF'} under ${limit} MB.`); return;
      }
      const localUri = await importPickedFile(asset.uri, asset.name);
      setError(undefined);
      if (kind === 'audio') update({ audioUri: localUri, audioName: asset.name, audioSize: asset.size, audioType: asset.mimeType ?? 'application/octet-stream' });
      else update({ pdfUri: localUri, pdfName: asset.name, syllabusText: '' });
    } catch (cause) {
      console.error('Failed to import selected file', cause);
      setError('That file could not be read. Select it again from your phone’s Files app.');
    }
  }

  async function makeNotes() {
    if (guard.current) return;
    if (!draft.audioUri) { setError('Add a recording first.'); return; }
    if (!draft.topics.trim()) { setError('Tell us what was covered in this class.'); return; }
    guard.current = true;
    setBusy(true);
    setError(undefined);
    try {
      const saved = await createClass(draft, API_URL);
      await refresh();
      void submit(saved.id);
      router.replace({ pathname: '/processing/[id]', params: { id: saved.id } });
    } catch (cause) {
      console.error('Failed to save class locally', cause);
      setError('The selected recording could not be saved. Select it again, then retry.');
    } finally { guard.current = false; setBusy(false); }
  }

  return <SafeAreaView style={ui.screen} edges={['bottom', 'left', 'right']}>
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'} keyboardVerticalOffset={90}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={ui.content}>
        <Entrance><Text style={ui.title}>Let’s make sense of class.</Text>
          <Text style={ui.text}>Start with your recording. A few topics help put the words in context.</Text></Entrance>
        {server?.mock && <MockNotice />}
        <View style={ui.card}>
          <Text style={ui.eyebrow}>01 · THE RECORDING</Text>
          <Text style={ui.heading}>{draft.audioName || 'Bring the whole conversation.'}</Text>
          {draft.audioSize != null && <Text style={ui.muted}>{(draft.audioSize / 1024 / 1024).toFixed(1)} MB · {draft.audioType}</Text>}
          <Text style={ui.muted}>Choose a file from your phone’s recorder. M4A, MP3, WAV, and other common audio formats.</Text>
          <Button label={draft.audioUri ? 'Change recording' : 'Add recording'} secondary onPress={() => { void pick('audio'); }} disabled={busy} />
          {server && <Text style={ui.muted}>Up to {server.max_audio_mb} MB · {Math.floor(server.max_duration_seconds / 60)} minutes</Text>}
        </View>
        <View style={{ gap: 10 }}>
          <Text style={ui.label}>What was covered?</Text>
          <TextInput accessibilityLabel="What was covered? Required" style={[ui.input, { minHeight: 120 }]} multiline
            placeholder="A few topics, terms, or questions from today…" placeholderTextColor={colors.muted}
            value={draft.topics} onChangeText={topics => update({ topics })} maxLength={4000} editable={!busy} />
          <Text style={ui.muted}>Notes come from the recording. Topics only provide context.</Text>
        </View>
        <View style={{ gap: 10 }}><Text style={ui.label}>Class title — optional</Text>
          <TextInput accessibilityLabel="Class title, optional" style={ui.input} placeholder="Give this class a name" placeholderTextColor={colors.muted}
            value={draft.title} onChangeText={title => update({ title })} maxLength={160} editable={!busy} />
        </View>
        <View style={ui.card}><Text style={ui.label}>Add syllabus — optional</Text>
          <Text style={ui.muted}>Paste the relevant part, or attach a text-based PDF.</Text>
          {draft.pdfUri ? <><Text style={ui.text}>{draft.pdfName}</Text>
            <Button label="Remove PDF" secondary disabled={busy} onPress={() => update({ pdfUri: undefined, pdfName: undefined })} /></> : <>
            <TextInput accessibilityLabel="Syllabus text, optional" style={[ui.input, { minHeight: 110 }]} multiline
              placeholder="Paste syllabus text here…" placeholderTextColor={colors.muted} value={draft.syllabusText}
              onChangeText={syllabusText => update({ syllabusText })} maxLength={100000} editable={!busy} />
            <Button label="Attach a PDF" secondary disabled={busy || !!draft.syllabusText.trim()} onPress={() => { void pick('pdf'); }} />
          </>}
        </View>
        <ErrorMessage message={error} />
        <Button label={busy ? 'Saving recording…' : 'Make notes'} onPress={() => { void makeNotes(); }} busy={busy} />
        <Text style={ui.muted}>Keep the app open while uploading. Once uploaded, you can leave while your notes are prepared.</Text>
        <Text style={ui.muted}>Your recording is sent to the configured server and removed after processing. Notes are kept there temporarily so you can download them.</Text>
      </ScrollView>
    </KeyboardAvoidingView>
  </SafeAreaView>;
}
