import { useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { ActivityIndicator, Alert, KeyboardAvoidingView, Modal, Platform, ScrollView, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useClasses } from '../../src/classes';
import { ApiError, deleteRemote } from '../../src/api';
import { Button, ErrorMessage, MockNotice } from '../../src/components';
import { patchClass, removeLocalClass } from '../../src/storage';
import { ui } from '../../src/theme';
import { Bullet, SavedClass } from '../../src/types';
import { BulletEditor, validBullets } from '../../src/BulletEditor';
import { StudySection } from '../../src/StudySection';
import { InsightTabs, LectureTab } from '../../src/InsightTabs';
import { LectureInsightsPanel } from '../../src/LectureInsights';
import { ReconnectServer } from '../../src/ReconnectServer';

export default function ClassDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { items, loading } = useClasses();
  const item = items.find(value => value.id === id);
  if (!item?.result) return <View style={[ui.screen, ui.content]}>{loading ? <ActivityIndicator /> : <Text style={ui.text}>These notes are not available on this phone yet.</Text>}</View>;
  return <Detail item={item} />;
}

function Detail({ item }: { item: SavedClass }) {
  const { refresh } = useClasses();
  const output = item.result!;
  const [editor, setEditor] = useState<{ index: number; heading: string; subheading: string; body: string; bullets: Bullet[] }>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [serverBusy, setServerBusy] = useState(false);
  const [tab, setTab] = useState<LectureTab>('notes');

  async function saveEdit() {
    if (!editor || saving) return;
    if (!editor.heading.trim() || !validBullets(editor.bullets) || !(editor.body.trim() || editor.bullets.length || output.notes.sections[editor.index]?.comparison)) {
      setError('Keep a heading and study content. Fill in or remove empty bullets and details.'); return;
    }
    setSaving(true);
    try {
      await patchClass(item.id, { edits: { ...item.edits, [String(editor.index)]: {
        heading: editor.heading.trim(), subheading: editor.subheading.trim(), body: editor.body.trim(),
        bullets: editor.bullets,
      } } });
      await refresh(); setEditor(undefined); setError(undefined);
    } catch { setError('Your edit could not be saved. Check device storage and try again.'); }
    finally { setSaving(false); }
  }

  async function removeServer() {
    setServerBusy(true);
    try {
      try { await deleteRemote(item); }
      catch (e) { if (!(e instanceof ApiError && [404, 410].includes(e.status))) throw e; }
      await patchClass(item.id, { serverDeleted: true }); await refresh();
    } catch { setError('Could not remove the server copy. Check your connection and try again.'); }
    finally { setServerBusy(false); }
  }

  function removeLocal() {
    Alert.alert('Remove from this phone?', 'The recording, notes, and your edits will be deleted from this phone. Any server copy still expires automatically.', [
      { text: 'Keep class', style: 'cancel' }, { text: 'Remove', style: 'destructive', onPress: () => {
        void removeLocalClass(item.id).then(refresh).then(() => router.replace('/')).catch(() => setError('Could not remove this class. Try again.'));
      } },
    ]);
  }

  const header = <View style={{ gap: 16 }}>
    <Text style={ui.eyebrow}>LECTURE NOTES</Text><Text style={ui.title}>{output.notes.title}</Text>
    <Text style={ui.muted}>{new Date(item.createdAt).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })}</Text>
    {output.mock && <MockNotice />}
    <ErrorMessage message={error} />
  </View>;

  return <SafeAreaView style={ui.screen} edges={['bottom', 'left', 'right']}>
    <ScrollView contentContainerStyle={ui.content}>
      {header}
      <ReconnectServer item={item} onConnected={refresh} />
      <InsightTabs value={tab} onChange={setTab} />
      {tab === 'insights' ? <LectureInsightsPanel key={`${item.id}:${item.apiUrl}`} item={item} onSaved={refresh} /> : output.notes.sections.map((section, index) => {
        const edit = item.edits[String(index)];
        const previous = output.notes.sections[index - 1];
        const previousHeading = item.edits[String(index - 1)]?.heading ?? previous?.heading;
        return <View key={index} style={ui.card}>
          <StudySection section={section} edit={edit} showHeading={(edit?.heading ?? section.heading) !== previousHeading} />
          {edit && <Text style={ui.muted}>Edited by you</Text>}
          <Button label="Edit section" accessibilityLabel={`Edit ${edit?.heading ?? section.heading}`} secondary onPress={() => { setError(undefined); setEditor({ index, heading: edit?.heading ?? section.heading, subheading: edit?.subheading ?? section.subheading ?? '', body: edit?.body ?? section.body, bullets: edit?.bullets ?? section.bullets ?? [] }); }} />
        </View>;
      })}
      <View style={ui.divider} /><Text style={ui.muted}>Notes and edits are saved on this phone.</Text>
      {item.serverDeleted ? <Text style={ui.muted}>Server copy removed.</Text> : <>
        <Text style={ui.muted}>Server copy expires {item.expiresAt ? new Date(item.expiresAt * 1000).toLocaleString() : 'automatically'}.</Text>
        <Button label="Remove server copy" secondary busy={serverBusy} onPress={() => { void removeServer(); }} />
      </>}
      <Button label="Remove from this phone" secondary onPress={removeLocal} />
    </ScrollView>
    <Modal visible={!!editor} animationType="slide" onRequestClose={() => { if (!saving) setEditor(undefined); }}>
      <SafeAreaView style={ui.screen}><KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={ui.content}>
          <Text style={ui.heading}>Make it yours</Text>
          <Text style={ui.muted}>Your edits are saved locally. Comparison tables are kept unchanged.</Text>
          <TextInput accessibilityLabel="Section heading" style={ui.input} value={editor?.heading} maxLength={200}
            onChangeText={heading => setEditor(current => current ? { ...current, heading } : undefined)} editable={!saving} />
          <TextInput accessibilityLabel="Additional explanation (optional)" style={ui.input} multiline value={editor?.body} maxLength={16000}
            onChangeText={body => setEditor(current => current ? { ...current, body } : undefined)} editable={!saving} />
          <TextInput accessibilityLabel="Subtopic" style={ui.input} value={editor?.subheading} maxLength={200}
            onChangeText={subheading => setEditor(current => current ? { ...current, subheading } : undefined)} editable={!saving} />
          <BulletEditor value={editor?.bullets ?? []} disabled={saving}
            onChange={bullets => setEditor(current => current ? { ...current, bullets } : undefined)} />
          <ErrorMessage message={error} /><Button label="Save changes" busy={saving} onPress={() => { void saveEdit(); }} />
          <Button label="Discard changes" secondary disabled={saving} onPress={() => setEditor(undefined)} />
        </ScrollView>
      </KeyboardAvoidingView></SafeAreaView>
    </Modal>
  </SafeAreaView>;
}
