import { TextInput, View } from 'react-native';
import { Button } from './components';
import { ui } from './theme';
import { Bullet } from './types';

export function validBullets(bullets: Bullet[]): boolean {
  return bullets.every(b => typeof b === 'string' ? !!b.trim() :
    !!b.text.trim() && b.children.length > 0 && b.children.every(c => !!c.trim()));
}

// Keep parent/child strings intact, including multiline equations and examples.
export function BulletEditor({ value, onChange, disabled }: {
  value: Bullet[]; onChange: (value: Bullet[]) => void; disabled: boolean;
}) {
  const replace = (i: number, bullet: Bullet) => onChange(value.map((b, j) => j === i ? bullet : b));
  return <View style={{ gap: 16 }}>
    {value.map((bullet, i) => <View key={i} style={{ gap: 8 }}>
      <TextInput accessibilityLabel={`Bullet ${i + 1}`} style={ui.input} multiline editable={!disabled}
        value={typeof bullet === 'string' ? bullet : bullet.text}
        onChangeText={text => replace(i, typeof bullet === 'string' ? text : { ...bullet, text })} />
      {typeof bullet !== 'string' && bullet.children.map((child, j) => <View key={j} style={{ marginLeft: 24, gap: 8 }}>
        <TextInput accessibilityLabel={`Bullet ${i + 1}, detail ${j + 1}`} style={ui.input} multiline editable={!disabled}
          value={child} onChangeText={text => replace(i, { ...bullet, children: bullet.children.map((c, k) => k === j ? text : c) })} />
        <Button label="Remove detail" secondary disabled={disabled} onPress={() => {
          const children = bullet.children.filter((_, k) => k !== j);
          replace(i, children.length ? { ...bullet, children } : bullet.text);
        }} />
      </View>)}
      <Button label="Add detail" secondary disabled={disabled} onPress={() => replace(i,
        typeof bullet === 'string' ? { text: bullet, children: [''] } : { ...bullet, children: [...bullet.children, ''] })} />
      <Button label="Remove bullet" secondary disabled={disabled} onPress={() => onChange(value.filter((_, j) => i !== j))} />
    </View>)}
    <Button label="Add bullet" secondary disabled={disabled} onPress={() => onChange([...value, ''])} />
  </View>;
}
