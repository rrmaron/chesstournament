import React from 'react';
import { render, fireEvent, waitFor, act } from '@testing-library/react-native';
import SearchScreen from '../screens/SearchScreen';

describe('SearchScreen', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.clearAllMocks();
  });

  test('does not search until at least 3 characters are typed', () => {
    const navigation = { navigate: jest.fn() };
    const { getByPlaceholderText } = render(<SearchScreen navigation={navigation} />);
    fireEvent.changeText(getByPlaceholderText('Type a player name…'), 'ab');
    act(() => { jest.advanceTimersByTime(500); });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  test('searches (debounced) once 3+ characters are typed, hitting the right URL', async () => {
    global.fetch.mockResolvedValue({
      json: async () => [{ uscf_id: '12345678', name: 'Doe, Jane', rating: 1500 }],
    });
    const navigation = { navigate: jest.fn() };
    const { getByPlaceholderText, findByText } = render(<SearchScreen navigation={navigation} />);
    fireEvent.changeText(getByPlaceholderText('Type a player name…'), 'Jane');

    expect(global.fetch).not.toHaveBeenCalled(); // debounced, not immediate

    await act(async () => { jest.advanceTimersByTime(400); });

    expect(global.fetch).toHaveBeenCalledWith(
      'https://mychessrating.fly.dev/api/public/player-search?name=Jane'
    );
    expect(await findByText('Doe, Jane')).toBeTruthy();
  });

  test('a fetch failure clears results instead of crashing', async () => {
    global.fetch.mockRejectedValue(new Error('network down'));
    const navigation = { navigate: jest.fn() };
    const { getByPlaceholderText, findByText } = render(<SearchScreen navigation={navigation} />);
    fireEvent.changeText(getByPlaceholderText('Type a player name…'), 'xyz');
    await act(async () => { jest.advanceTimersByTime(400); });
    expect(await findByText('No players found')).toBeTruthy();
  });

  test('selecting a result navigates to the Player screen with its uscf_id and name', async () => {
    global.fetch.mockResolvedValue({
      json: async () => [{ uscf_id: '12345678', name: 'Doe, Jane', rating: 1500 }],
    });
    const navigation = { navigate: jest.fn() };
    const { getByPlaceholderText, findByText } = render(<SearchScreen navigation={navigation} />);
    fireEvent.changeText(getByPlaceholderText('Type a player name…'), 'Jane');
    await act(async () => { jest.advanceTimersByTime(400); });

    const result = await findByText('Doe, Jane');
    fireEvent.press(result);

    expect(navigation.navigate).toHaveBeenCalledWith('Player', { uscf_id: '12345678', name: 'Doe, Jane' });
  });
});
