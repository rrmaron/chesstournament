import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, ScrollView, StyleSheet,
  ActivityIndicator, Linking, TouchableOpacity,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { loadMyRatings } from './MyRatingsScreen';

const API_BASE = 'https://mychessrating.fly.dev';

// ---------------------------------------------------------------------------
// Rating impact helpers
// ---------------------------------------------------------------------------
function eloImpact(myRating, oppRating, k) {
  const expected = 1 / (1 + Math.pow(10, (oppRating - myRating) / 400));
  return {
    win:  Math.round(k * (1 - expected)),
    draw: Math.round(k * (0.5 - expected)),
    loss: Math.round(k * (0 - expected)),
    pct:  Math.round(expected * 100),
  };
}
const uscfK = (r) => r < 2100 ? 32 : r < 2400 ? 24 : 16;
const fideK = (r) => r < 1600 ? 40 : r < 2400 ? 20 : 10;

// ---------------------------------------------------------------------------
// Impact table component
// ---------------------------------------------------------------------------
function ImpactTable({ myRatings, player, navigation }) {
  const myUscf = myRatings?.uscfRating;
  const myFide = myRatings?.fideRating;
  const oppUscf = player?.live_uscf_rating || player?.uscf_rating;
  const oppFide = player?.fide_rating;

  const hasProfile = myUscf || myFide;

  if (!hasProfile) {
    return (
      <View style={impactStyles.nudge}>
        <Text style={impactStyles.nudgeText}>
          Set your ratings to see how this game would affect your score.
        </Text>
        <TouchableOpacity
          style={impactStyles.nudgeBtn}
          onPress={() => navigation.navigate('MyRatings')}
        >
          <Text style={impactStyles.nudgeBtnText}>Set My Ratings →</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const uscfImpact = (myUscf && oppUscf) ? eloImpact(myUscf, oppUscf, uscfK(myUscf)) : null;
  const fideImpact = (myFide && oppFide) ? eloImpact(myFide, oppFide, fideK(myFide)) : null;

  if (!uscfImpact && !fideImpact) {
    return (
      <View style={impactStyles.nudge}>
        <Text style={impactStyles.nudgeText}>
          No matching ratings to compare. This player may not have a{!oppFide ? ' FIDE' : ''}{!oppUscf ? ' USCF' : ''} rating on file.
        </Text>
      </View>
    );
  }

  const rows = [
    { label: 'Win',  color: '#2e7d32', bg: '#f1f8e9', uscf: uscfImpact?.win,  fide: fideImpact?.win,  sign: true },
    { label: 'Draw', color: '#e65100', bg: '#fff8e1', uscf: uscfImpact?.draw, fide: fideImpact?.draw, sign: true },
    { label: 'Loss', color: '#c62828', bg: '#ffebee', uscf: uscfImpact?.loss, fide: fideImpact?.loss, sign: false },
  ];

  const fmt = (n, sign) => {
    if (n === undefined || n === null) return '—';
    return (sign && n > 0 ? '+' : '') + n;
  };

  return (
    <View style={impactStyles.container}>
      {/* Header */}
      <View style={impactStyles.headerRow}>
        <Text style={[impactStyles.headerCell, { flex: 1 }]}>Outcome</Text>
        {uscfImpact && (
          <Text style={impactStyles.headerCell}>
            Live USCF{'\n'}
            <Text style={impactStyles.subHeader}>Me:{myUscf} / Opp:{oppUscf}</Text>
          </Text>
        )}
        {fideImpact && (
          <Text style={impactStyles.headerCell}>
            FIDE{'\n'}
            <Text style={impactStyles.subHeader}>Me:{myFide} / Opp:{oppFide}</Text>
          </Text>
        )}
      </View>

      {rows.map((row) => (
        <View key={row.label} style={[impactStyles.row, { backgroundColor: row.bg }]}>
          <Text style={[impactStyles.outcomeLabel, { color: row.color, flex: 1 }]}>{row.label}</Text>
          {uscfImpact && (
            <Text style={[impactStyles.value, { color: row.uscf >= 0 ? '#2e7d32' : '#c62828' }]}>
              {fmt(row.uscf, row.sign)}
            </Text>
          )}
          {fideImpact && (
            <Text style={[impactStyles.value, { color: row.fide >= 0 ? '#2e7d32' : '#c62828' }]}>
              {fmt(row.fide, row.sign)}
            </Text>
          )}
        </View>
      ))}

      {/* Expected score */}
      <View style={impactStyles.footerRow}>
        <Text style={[impactStyles.footerCell, { flex: 1 }]}>Expected score</Text>
        {uscfImpact && <Text style={impactStyles.footerCell}>{uscfImpact.pct}%</Text>}
        {fideImpact && <Text style={impactStyles.footerCell}>{fideImpact.pct}%</Text>}
      </View>

      <Text style={impactStyles.disclaimer}>
        Estimates only — actual changes depend on the full event.
      </Text>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------
export default function PlayerScreen({ route, navigation }) {
  const { uscf_id } = route.params;
  const [player, setPlayer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [myRatings, setMyRatings] = useState(null);
  const [showImpact, setShowImpact] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(
          `${API_BASE}/api/public/player-details?uscf_id=${encodeURIComponent(uscf_id)}`
        );
        const data = await r.json();
        if (!data || !data.name) setError('Player not found.');
        else setPlayer(data);
      } catch {
        setError('Could not load player — check your connection.');
      }
      setLoading(false);
    })();
  }, [uscf_id]);

  // Reload ratings when screen comes back into focus (e.g. after saving in MyRatings)
  useFocusEffect(
    useCallback(() => {
      loadMyRatings().then(setMyRatings);
    }, [])
  );

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

      {/* Rating impact button */}
      <TouchableOpacity
        style={styles.impactBtn}
        onPress={() => setShowImpact(!showImpact)}
      >
        <Text style={styles.impactBtnText}>
          {showImpact ? 'Hide rating impact ▲' : 'What happens to my ratings? ▼'}
        </Text>
      </TouchableOpacity>

      {showImpact && (
        <ImpactTable myRatings={myRatings} player={player} navigation={navigation} />
      )}

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
  impactBtn: {
    backgroundColor: '#1a1a2e',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
    marginTop: 16,
    marginBottom: 4,
  },
  impactBtnText: { color: '#fff', fontSize: 15, fontWeight: '600' },
  footer: { marginTop: 24, fontSize: 12, color: '#aaa', textAlign: 'center' },
});

const impactStyles = StyleSheet.create({
  container: {
    backgroundColor: '#fff',
    borderRadius: 12,
    overflow: 'hidden',
    marginTop: 4,
    marginBottom: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.08,
    shadowRadius: 4,
    elevation: 2,
  },
  headerRow: {
    flexDirection: 'row',
    backgroundColor: '#f5f5f5',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#e0e0e0',
  },
  headerCell: {
    fontSize: 13,
    fontWeight: '700',
    color: '#333',
    textAlign: 'center',
    minWidth: 80,
  },
  subHeader: { fontSize: 10, fontWeight: '400', color: '#888' },
  row: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: 1,
    borderBottomColor: '#eee',
  },
  outcomeLabel: { fontSize: 15, fontWeight: '700' },
  value: { fontSize: 16, fontWeight: '700', textAlign: 'center', minWidth: 80 },
  footerRow: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: '#fafafa',
  },
  footerCell: { fontSize: 12, color: '#888', textAlign: 'center', minWidth: 80, flex: undefined },
  disclaimer: { fontSize: 11, color: '#bbb', textAlign: 'center', padding: 8 },
  nudge: {
    padding: 16,
    alignItems: 'center',
  },
  nudgeText: { fontSize: 14, color: '#555', textAlign: 'center', marginBottom: 12 },
  nudgeBtn: {
    backgroundColor: '#1a1a2e',
    borderRadius: 8,
    paddingHorizontal: 20,
    paddingVertical: 10,
  },
  nudgeBtnText: { color: '#fff', fontWeight: '600' },
});
