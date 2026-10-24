// Minimal design tokens for the Companion Chat MVP.
// Intentionally self-contained — NOT imported from SelfMind. Swap freely.

export const C = {
  bg: '#0E0F13',
  surface: '#191B22',
  surface2: '#22252F',
  line: 'rgba(255,255,255,0.08)',
  text: '#F3F4F8',
  text2: '#A4A8B6',
  text3: '#6E7280',
  accent: '#F4A988',
  bubbleUser: '#F4A988',
  bubbleUserText: '#1A1203',
  bubbleAsst: '#22252F',
} as const;

export const RADIUS = { card: 18, bubble: 20, chip: 999 } as const;
export const SPACE = { xs: 6, sm: 10, md: 16, lg: 24 } as const;
export const TYPE = {
  title: { fontSize: 26, fontWeight: '700' as const, color: C.text },
  body: { fontSize: 16, color: C.text },
  meta: { fontSize: 13, color: C.text2 },
};
