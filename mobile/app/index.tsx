import { router } from 'expo-router';
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Button, Entrance, ErrorMessage } from '../src/components';
import { useClasses } from '../src/classes';
import { colors, stageLabel, ui } from '../src/theme';

export default function Home() {
  const { items, loading, storageError, sync } = useClasses();
  return <SafeAreaView style={ui.screen} edges={['top', 'left', 'right', 'bottom']}>
    <FlatList data={items} keyExtractor={item => item.id} contentContainerStyle={ui.content}
      refreshControl={<RefreshControl refreshing={false} onRefresh={() => { void sync(); }} tintColor={colors.accent} />}
      ListHeaderComponent={<Entrance><View style={{ paddingTop: 18, gap: 12 }}>
        <Text style={ui.eyebrow}>LAZY NOTES</Text><Text style={ui.title}>A little less scribbling.</Text>
        <Text style={ui.text}>Your classes, made clearer. Keep the ideas, find the words, make them yours.</Text>
      </View><Button label="＋  New class" accessibilityLabel="New class" onPress={() => router.push('/new')} />
        <ErrorMessage message={storageError} />
        <View style={[ui.row, { justifyContent: 'space-between', marginTop: 10 }]}>
          <Text style={ui.heading}>Your classes</Text><Text style={ui.muted}>Saved on this phone</Text>
        </View></Entrance>}
      ListEmptyComponent={loading ? <ActivityIndicator color={colors.accent} /> : <View style={[ui.card, { marginTop: 20, paddingVertical: 32 }]}>
        <View style={styles.paperMark}><Text style={{ fontSize: 28, color: colors.accent }}>≡</Text></View>
        <Text style={ui.heading}>Room for your next class.</Text>
        <Text style={ui.text}>Add a recording from your phone, tell us what was covered, and come back to clear, editable notes.</Text>
        <Text style={ui.muted}>No subjects to set up. Just one class at a time.</Text>
      </View>}
      renderItem={({ item }) => <Pressable accessibilityRole="button"
        accessibilityLabel={`${item.title}, ${stageLabel[item.stage]}`}
        onPress={() => router.push({ pathname: item.result ? '/class/[id]' : '/processing/[id]', params: { id: item.id } })}
        style={({ pressed }) => [ui.card, { marginTop: 14, opacity: pressed ? 0.75 : 1 }]}>
        <Text style={ui.heading}>{item.title}</Text>
        <Text style={ui.muted}>{new Date(item.createdAt).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</Text>
        <Text style={{ color: colors.accent, fontSize: 15, fontWeight: '600' }}>{stageLabel[item.stage]}  →</Text>
        {item.mock && <Text style={ui.muted}>Development example</Text>}
      </Pressable>}
      ListFooterComponent={items.length ? <Text style={[ui.muted, { marginTop: 20 }]}>Your downloaded notes stay here, even after the server copy expires.</Text> : undefined}
    />
  </SafeAreaView>;
}

const styles = StyleSheet.create({ paperMark: { width: 56, height: 64, borderRadius: 8, backgroundColor: colors.soft,
  alignItems: 'center', justifyContent: 'center', marginBottom: 8, transform: [{ rotate: '-5deg' }] } });
