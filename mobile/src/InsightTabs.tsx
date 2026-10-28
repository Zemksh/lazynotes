import { Pressable, Text, View } from 'react-native';
import { colors, ui } from './theme';

export type LectureTab = 'notes' | 'insights';
export function InsightTabs({ value, onChange }: { value: LectureTab; onChange: (value: LectureTab) => void }) {
  return <View accessibilityRole="tablist" style={{ flexDirection: 'row', padding: 4, borderRadius: 16, backgroundColor: colors.soft }}>
    {(['notes', 'insights'] as const).map(tab => <Pressable key={tab} accessibilityRole="tab"
      accessibilityState={{ selected: value === tab }} onPress={() => onChange(tab)}
      style={{ flex: 1, minHeight: 48, justifyContent: 'center', padding: 10, borderRadius: 12,
        backgroundColor: value === tab ? colors.card : 'transparent' }}>
      <Text style={[ui.label, { textAlign: 'center', color: value === tab ? colors.accent : colors.muted }]}>
        {tab === 'notes' ? 'Study Notes' : 'Lecture Insights'}
      </Text>
    </Pressable>)}
  </View>;
}
