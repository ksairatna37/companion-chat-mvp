// Chat with one companion. Loads history + story, sends messages, shows gem balance.
// Optimistic send with rollback on failure; inverted FlashList for chat.

import { useEffect, useRef, useState, useCallback } from 'react';
import {
  View, Text, TextInput, Pressable, StyleSheet,
  KeyboardAvoidingView, Platform, ActivityIndicator,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { FlashList } from '@shopify/flash-list';
import { api, type Character, type Message, type Story } from '../lib/api';
import { C, RADIUS, SPACE, TYPE } from '../lib/tokens';

export default function ChatScreen({ character, onBack }: { character: Character; onBack: () => void }) {
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>([]);
  const [story, setStory] = useState<Story | null>(null);
  const [gems, setGems] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const tempId = useRef(0);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [hist, st, w] = await Promise.all([
        api.getMessages(character.id),
        api.getStory(character.id),
        api.getWallet(),
      ]);
      if (!alive) return;
      setStory(st);
      setGems(w.gems);
      // newest-first for an inverted list; show opener when empty
      setMessages(hist.messages);
    })().catch((e) => alive && setNotice(e.message));
    return () => {
      alive = false;
    };
  }, [character.id]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setDraft('');
    setSending(true);
    setNotice(null);

    const optimistic: Message = { id: `tmp-${tempId.current++}`, role: 'user', text };
    setMessages((m) => [optimistic, ...m]);

    try {
      const r = await api.sendMessage(character.id, text);
      setGems(r.wallet.gems);
      setStory((s) => (s ? { ...s, current_beat: r.story.current_beat, relationship: r.story.relationship } : s));
      setMessages((m) => [
        { id: r.assistant_message.id, role: 'assistant', text: r.assistant_message.text, beat: r.assistant_message.beat },
        { ...optimistic, id: r.user_message.id },
        ...m.filter((x) => x.id !== optimistic.id),
      ]);
    } catch (e: any) {
      setMessages((m) => m.filter((x) => x.id !== optimistic.id)); // rollback
      setDraft(text);
      setNotice(e.code === 'insufficient_gems' ? 'Out of gems. Claim your daily bonus.' : e.message);
    } finally {
      setSending(false);
    }
  }, [draft, sending, character.id]);

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <View style={[styles.header, { paddingTop: insets.top + SPACE.xs }]}>
        <Pressable onPress={onBack} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back">
          <Text style={styles.back}>‹</Text>
        </Pressable>
        <Text style={styles.name}>{character.name}</Text>
        <Text style={styles.gem}>{gems ?? '·'} 💎</Text>
      </View>

      <FlashList
        data={messages}
        inverted
        keyExtractor={(m) => m.id}
        estimatedItemSize={64}
        contentContainerStyle={{ padding: SPACE.md }}
        ListFooterComponent={
          story ? <Text style={styles.opener}>{story.opener}</Text> : <ActivityIndicator color={C.accent} />
        }
        renderItem={({ item }) => <Bubble m={item} />}
      />

      {notice && <Text style={styles.notice}>{notice}</Text>}

      <View style={[styles.composer, { paddingBottom: insets.bottom + SPACE.sm }]}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          placeholder={`Message ${character.name}…`}
          placeholderTextColor={C.text3}
          style={styles.input}
          multiline
          onSubmitEditing={send}
        />
        <Pressable
          onPress={send}
          disabled={sending || !draft.trim()}
          style={({ pressed }) => [styles.sendBtn, (sending || !draft.trim()) && { opacity: 0.4 }, pressed && { opacity: 0.8 }]}
          accessibilityRole="button"
          accessibilityLabel="Send"
        >
          {sending ? <ActivityIndicator color={C.bubbleUserText} /> : <Text style={styles.sendText}>Send</Text>}
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

function Bubble({ m }: { m: Message }) {
  const mine = m.role === 'user';
  return (
    <View style={[styles.row, { justifyContent: mine ? 'flex-end' : 'flex-start' }]}>
      <View style={[styles.bubble, mine ? styles.mine : styles.theirs]}>
        <Text style={[TYPE.body, mine && { color: C.bubbleUserText }]}>{m.text}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACE.md,
    paddingHorizontal: SPACE.md,
    paddingBottom: SPACE.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: C.line,
  },
  back: { color: C.accent, fontSize: 34, lineHeight: 34, width: 24 },
  name: { ...TYPE.body, fontWeight: '700', flex: 1 },
  gem: { color: C.text2 },
  opener: { ...TYPE.meta, fontStyle: 'italic', textAlign: 'center', marginVertical: SPACE.lg },
  row: { marginVertical: SPACE.xs / 2 },
  bubble: { maxWidth: '82%', borderRadius: RADIUS.bubble, paddingHorizontal: SPACE.md, paddingVertical: SPACE.sm },
  mine: { backgroundColor: C.bubbleUser, borderBottomRightRadius: 6 },
  theirs: { backgroundColor: C.bubbleAsst, borderBottomLeftRadius: 6 },
  notice: { color: C.accent, textAlign: 'center', paddingVertical: SPACE.xs },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: SPACE.sm,
    paddingHorizontal: SPACE.md,
    paddingTop: SPACE.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: C.line,
  },
  input: {
    flex: 1,
    color: C.text,
    backgroundColor: C.surface,
    borderRadius: RADIUS.card,
    paddingHorizontal: SPACE.md,
    paddingTop: SPACE.sm,
    paddingBottom: SPACE.sm,
    maxHeight: 120,
  },
  sendBtn: {
    backgroundColor: C.accent,
    borderRadius: RADIUS.chip,
    paddingHorizontal: SPACE.md,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendText: { color: C.bubbleUserText, fontWeight: '700' },
});
