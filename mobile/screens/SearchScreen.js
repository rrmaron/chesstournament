import React, { useState, useRef } from 'react';
import {
  View, TextInput, FlatList, TouchableOpacity,
  Text, StyleSheet, ActivityIndicator, Keyboard,
} from 'react-native';

const API_BASE = 'https://mychessrating.fly.dev';

export default function SearchScreen({ navigation }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const debounceRef = useRef(null);

  const handleChange = (text) => {
    setQuery(text);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (text.length < 3) {
      setResults([]);
      setSearched(false);
      return;
    }
    debounceRef.current = setTimeout(() => doSearch(text), 400);
  };

  const doSearch = async (text) => {
    setLoading(true);
    setSearched(true);
    try {
      const r = await fetch(
        `${API_BASE}/api/public/player-search?name=${encodeURIComponent(text)}`
      );
      const data = await r.json();
      setResults(Array.isArray(data) ? data : []);
    } catch {
      setResults([]);
    }
    setLoading(false);
  };

  const selectPlayer = (item) => {
    Keyboard.dismiss();
    setResults([]);
    navigation.navigate('Player', { uscf_id: item.uscf_id, name: item.name });
  };

  return (
    <View style={styles.container}>
      <View style={styles.searchBox}>
        <TextInput
          style={styles.input}
          value={query}
          onChangeText={handleChange}
          placeholder="Type a player name…"
          placeholderTextColor="#999"
          autoCorrect={false}
          autoCapitalize="words"
          returnKeyType="search"
          clearButtonMode="while-editing"
        />
        {loading && (
          <ActivityIndicator style={styles.spinner} color="#1a1a2e" />
        )}
      </View>

      <Text style={styles.hint}>
        Type at least 3 characters — suggestions appear automatically
      </Text>

      {searched && !loading && results.length === 0 && (
        <Text style={styles.noResults}>No players found</Text>
      )}

      <FlatList
        data={results}
        keyExtractor={(item) => item.uscf_id}
        keyboardShouldPersistTaps="handled"
        renderItem={({ item }) => (
          <TouchableOpacity style={styles.item} onPress={() => selectPlayer(item)}>
            <Text style={styles.itemName}>{item.name}</Text>
            <Text style={styles.itemMeta}>
              USCF {item.uscf_id}
              {item.rating ? `  ·  Rating ${item.rating}` : ''}
            </Text>
          </TouchableOpacity>
        )}
        ItemSeparatorComponent={() => <View style={styles.separator} />}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f5f5' },
  searchBox: {
    backgroundColor: '#fff',
    margin: 12,
    borderRadius: 10,
    flexDirection: 'row',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 3,
    elevation: 2,
  },
  input: {
    flex: 1,
    padding: 14,
    fontSize: 16,
    color: '#111',
  },
  spinner: { marginRight: 12 },
  hint: {
    marginHorizontal: 16,
    marginBottom: 8,
    fontSize: 12,
    color: '#999',
  },
  noResults: {
    textAlign: 'center',
    marginTop: 32,
    color: '#888',
    fontSize: 15,
  },
  item: {
    backgroundColor: '#fff',
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  itemName: { fontSize: 16, fontWeight: '600', color: '#1a1a2e' },
  itemMeta: { fontSize: 13, color: '#666', marginTop: 2 },
  separator: { height: 1, backgroundColor: '#eee' },
});
