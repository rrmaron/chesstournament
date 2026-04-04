import React, { useState, useEffect } from 'react';
import {
  View, Text, ScrollView, StyleSheet,
  ActivityIndicator, Linking, TouchableOpacity,
} from 'react-native';

const API_BASE = 'https://mychessrating.fly.dev';

export default function PlayerScreen({ route }) {
  const { uscf_id } = route.params;
  const [player, setPlayer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(
          `${API_BASE}/api/public/player-details?uscf_id=${encodeURIComponent(uscf_id)}`
        );
        const data = await r.json();
        if (!data || !data.name) {
          setError('Player not found.');
        } else {
          setPlayer(data);
        }
      } catch {
        setError('Could not load player — check your connection.');
      }
      setLoading(false);
    })();
  }, [uscf_id]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#1a1a2e" />
        <Text style={styles.loadingText}>Fetching ratings…</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>{error}</Text>
      </View>
    );
  }

  const rows = [
    { label: 'USCF ID', value: player.uscf_id,
      link: `https://ratings.uschess.org/player/${player.uscf_id}` },
    { label: 'USCF Rating', value: player.uscf_rating || '—' },
    { label: 'Live USCF Rating', value: player.live_uscf_rating || '—' },
    { label: 'FIDE ID', value: player.fide_id || '—',
      link: player.fide_id ? `https://ratings.fide.com/profile/${player.fide_id}` : null },
    { label: 'FIDE Rating', value: player.fide_rating || (player.fide_id ? 'Not rated' : '—') },
    { label: 'USCF Expiry', value: player.expiry || '—' },
  ];

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.nameCard}>
        <Text style={styles.playerName}>{player.name}</Text>
      </View>

      <View style={styles.card}>
        {rows.map((row, i) => (
          <View key={row.label} style={[styles.row, i < rows.length - 1 && styles.rowBorder]}>
            <Text style={styles.label}>{row.label}</Text>
            {row.link ? (
              <TouchableOpacity onPress={() => Linking.openURL(row.link)}>
                <Text style={styles.link}>{row.value}</Text>
              </TouchableOpacity>
            ) : (
              <Text style={styles.value}>{String(row.value)}</Text>
            )}
          </View>
        ))}
      </View>

      <Text style={styles.footer}>
        Data sourced from USCF and FIDE. Ratings may be slightly delayed.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f5f5' },
  content: { padding: 16, paddingBottom: 40 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  loadingText: { marginTop: 12, color: '#666', fontSize: 15 },
  errorText: { color: '#c00', fontSize: 16, textAlign: 'center' },
  nameCard: {
    backgroundColor: '#1a1a2e',
    borderRadius: 12,
    padding: 20,
    marginBottom: 16,
    alignItems: 'center',
  },
  playerName: { color: '#fff', fontSize: 22, fontWeight: 'bold', textAlign: 'center' },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.08,
    shadowRadius: 4,
    elevation: 2,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  rowBorder: { borderBottomWidth: 1, borderBottomColor: '#f0f0f0' },
  label: { fontSize: 14, color: '#555', fontWeight: '500', flex: 1 },
  value: { fontSize: 15, color: '#1a1a2e', fontWeight: '600', textAlign: 'right' },
  link: { fontSize: 15, color: '#0066cc', fontWeight: '600', textDecorationLine: 'underline' },
  footer: { marginTop: 24, fontSize: 12, color: '#aaa', textAlign: 'center' },
});
