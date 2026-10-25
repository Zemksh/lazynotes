import { ScrollView, Text, View } from 'react-native';
import { colors, ui } from './theme';
import { Section, SectionEdit } from './types';

// Explicit presentation allowlist: never spread server diagnostics or source IDs.
export function StudySection({ section, edit, showHeading = true }: {
  section: Section; edit?: SectionEdit; showHeading?: boolean;
}) {
  const body = edit?.body ?? section.body;
  const subheading = (edit?.subheading ?? section.subheading) || (!showHeading ? 'Continued discussion' : '');
  return <View style={{ gap: 12 }}>
    {showHeading && <Text accessibilityRole="header" style={ui.heading}>{edit?.heading ?? section.heading}</Text>}
    {!!subheading && <Text accessibilityRole="header" style={ui.label}>{subheading}</Text>}
    {body.split(/\n+/).filter(p => p.trim()).map((paragraph, i) =>
      <Text key={`p${i}`} selectable style={ui.text}>{paragraph}</Text>)}
    {(edit?.bullets ?? section.bullets ?? []).map((bullet, i) =>
      <View key={`b${i}`} style={{ gap: 8 }}>
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <Text style={ui.text}>•</Text><Text selectable style={[ui.text, { flex: 1 }]}>{typeof bullet === 'string' ? bullet : bullet.text}</Text>
        </View>
        {typeof bullet !== 'string' && bullet.children.map((child, j) =>
          <View key={j} style={{ flexDirection: 'row', gap: 10, marginLeft: 24 }}>
            <Text style={ui.text}>◦</Text><Text selectable style={[ui.text, { flex: 1 }]}>{child}</Text>
          </View>)}
      </View>)}
    {section.comparison && <ScrollView horizontal accessibilityLabel="Comparison table">
      <View>
        {[section.comparison.columns, ...section.comparison.rows].map((row, i) =>
          <View key={i} style={{ flexDirection: 'row', backgroundColor: i === 0 ? colors.soft : colors.card }}>
            {row.map((cell, j) => <Text key={j} selectable style={[i === 0 ? ui.label : ui.text,
              { width: 180, padding: 12, borderWidth: 0.5, borderColor: colors.border }]}>{cell}</Text>)}
          </View>)}
      </View>
    </ScrollView>}
  </View>;
}
