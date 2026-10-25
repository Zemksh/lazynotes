import { StyleSheet } from 'react-native';

export const colors = {
  paper: '#F6F3EC', card: '#FFFDF8', ink: '#292E28', muted: '#62685F',
  accent: '#405C47', soft: '#E7EDE3', border: '#DDDCD1', warning: '#79521D', warningBg: '#F6ECD8',
  danger: '#9A3D31', white: '#FFFFFF',
};
export const ui = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.paper },
  content: { padding: 24, gap: 20, paddingBottom: 48, width: '100%', maxWidth: 720, alignSelf: 'center' },
  title: { fontSize: 34, lineHeight: 42, fontWeight: '600', color: colors.ink, letterSpacing: -0.8 },
  heading: { fontSize: 22, lineHeight: 29, fontWeight: '600', color: colors.ink },
  text: { fontSize: 17, lineHeight: 26, color: colors.ink },
  muted: { fontSize: 15, lineHeight: 23, color: colors.muted },
  eyebrow: { fontSize: 12, lineHeight: 18, letterSpacing: 1.7, fontWeight: '700', color: colors.accent },
  card: { backgroundColor: colors.card, borderRadius: 20, padding: 20, gap: 12, borderWidth: 1, borderColor: colors.border },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  label: { fontSize: 16, fontWeight: '600', lineHeight: 24, color: colors.ink },
  input: { borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, borderRadius: 14,
    padding: 16, minHeight: 52, fontSize: 17, lineHeight: 25, color: colors.ink, textAlignVertical: 'top' },
  error: { color: colors.danger, fontSize: 15, lineHeight: 23 },
  notice: { padding: 16, backgroundColor: colors.warningBg, borderRadius: 14, gap: 8 },
  divider: { height: 1, backgroundColor: colors.border },
});

export const stageLabel: Record<string, string> = {
  uploading: 'Uploading recording', queued: 'Waiting to begin', transcribing: 'Transcribing class',
  preparing: 'Preparing your notes', ready: 'Notes ready', failed: 'Needs your attention', expired: 'Server copy expired',
};
