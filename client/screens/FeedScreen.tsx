// Feed of companions. Tap one to open the chat.
// Expo + RN; FlashList for the list, Pressable for touch. No spring animation.

import { useEffect, useState } from 'react';
import { View, Text, Image, Pressable, StyleSheet, ActivityIndicator } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { FlashList } from '@shopify/flash-list';
import { api, type Character } from '../lib/api';
import { C, RADIUS, SPACE, TYPE } from '../lib/tokens';

export default function FeedScreen({ onOpen }: { onOpen: (c: Character) => void }) {
  const insets = useSafeAreaInsets();
  const [chars, setChars] = useState<Character[] | null>(null);
  const [gems, setGems] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [cs, w] = await Promise.all([api.listCharacters(), api.getWallet()]);
        if (!alive) return;
        setChars(cs.characters);
        setGems(w.gems);
      } catch (e: any) {
        if (alive) setError(e.message ?? 'Could not load');
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (error) return <Center><Text style={TYPE.meta}>{error}</Text></Center>;
  if (!chars) return <Center><ActivityIndicator color={C.accent} /></Center>;

  return (
    <View style={[styles.container, { paddingTop: insets.top + SPACE.md }]}>
      <View style={styles.header}>
        <Text style={TYPE.title}>Companions</Text>
        {gems != null && (
          <View style={styles.gems}>
            <Text style={styles.gemText}>{gems} 💎</Text>
          </View>
        )}
      </View>
      <FlashList
        data={chars}
        keyExtractor={(c) => c.id}
        estimatedItemSize={92}
        contentContainerStyle={{ padding: SPACE.md, paddingBottom: insets.bottom + SPACE.lg }}
        ItemSeparatorComponent={() => <View style={{ height: SPACE.sm }} />}
        renderItem={({ item }) => (
          <Pressable
            onPress={() => !item.locked && onOpen(item)}
            style={({ pressed }) => [styles.card, pressed && { opacity: 0.85 }]}
            accessibilityRole="button"
            accessibilityLabel={`Chat with ${item.name}`}
          >
            <Image source={{ uri: item.avatar_url }} style={styles.avatar} />
            <View style={{ flex: 1 }}>
              <Text style={styles.name}>{item.name}</Text>
              <Text style={TYPE.meta} numberOfLines={2}>{item.tagline}</Text>
            </View>
            {item.locked && <Text style={styles.lock}>🔒 {item.unlock_cost_gems}</Text>}
          </Pressable>
        )}
      />
    </View>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <View style={[styles.container, styles.center]}>{children}</View>;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  center: { alignItems: 'center', justifyContent: 'center' },
  header: {
    paddingHorizontal: SPACE.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  gems: { backgroundColor: C.surface2, borderRadius: RADIUS.chip, paddingHorizontal: 12, paddingVertical: 6 },
  gemText: { color: C.text, fontWeight: '600' },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACE.md,
    backgroundColor: C.surface,
    borderRadius: RADIUS.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: C.line,
    padding: SPACE.md,
  },
  avatar: { width: 56, height: 56, borderRadius: 28, backgroundColor: C.surface2 },
  name: { ...TYPE.body, fontWeight: '700', marginBottom: 2 },
  lock: { color: C.text2 },
});
