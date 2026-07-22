import { Platform, ScrollView, StyleSheet, View, Text } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ThemedView } from '@/components/themed-view';
import { WebBadge } from '@/components/web-badge';
import { BottomTabInset, MaxContentWidth, Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

import { TouchableOpacity } from 'react-native';
import React, { useState, useEffect } from 'react';

import { getLoadsMetaData, getLoadPower } from "../api/api.js";
import PowerGraph from "@/components/PowerGraph";

type Load = {
  load_id: number;
  name: string;
};

type PowerSample = {
  time: string;
  power: number;
};

export default function TabTwoScreen() {
  const safeAreaInsets = useSafeAreaInsets();

  const insets = {
    ...safeAreaInsets,
    bottom: safeAreaInsets.bottom + BottomTabInset + Spacing.three,
  };

  const theme = useTheme();

  const contentPlatformStyle = Platform.select({
    android: {
      paddingTop: insets.top,
      paddingLeft: insets.left,
      paddingRight: insets.right,
      paddingBottom: insets.bottom,
    },
    web: {
      paddingTop: Spacing.six,
      paddingBottom: Spacing.four,
    },
  });

  const [timeRange, setTimeRange] = useState("Day");
  const [loadMetadata, setLoadMetadata] = useState<Load[]>([]);
  const [loadGraphs, setLoadGraphs] = useState<Record<number, PowerSample[]>>({});

  useEffect(() => {
    async function fetchData() {
      const result = await getLoadsMetaData();
      setLoadMetadata(result);
    }

    fetchData();
  }, []);

  useEffect(() => {
    async function fetchGraphData() {
      for (const load of loadMetadata) {
        const result = await getLoadPower(
          load.load_id,
          timeRange
        );

        setLoadGraphs(prev => ({
          ...prev,
          [load.load_id]: result
        }));
      }
    }

    if (loadMetadata.length === 0) {
      return;
    }

    fetchGraphData();

    const interval = setInterval(() => {
      fetchGraphData();
    }, 60000);

    return () => clearInterval(interval);

  }, [timeRange, loadMetadata]);

  return (
    <ScrollView
      style={[styles.scrollView, { backgroundColor: theme.background }]}
      contentInset={insets}
      contentContainerStyle={[styles.contentContainer, contentPlatformStyle]}
    >
      <ThemedView style={styles.container}>

        <View style={styles.segmentContainer}>
          {["Day", "Week", "Month", "6 Months", "Year"].map((range) => (
            <TouchableOpacity
              key={range}
              onPress={() => setTimeRange(range)}
              style={[
                styles.segment,
                timeRange === range && styles.selectedSegment
              ]}
            >
              <Text
                style={[
                  styles.segmentText,
                  timeRange === range && styles.selectedText
                ]}
              >
                {range}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        <View style={styles.loadsContainer}>
          {loadMetadata.map((load) => (
            <View
              key={load.load_id}
              style={styles.loadCard}
            >
              <Text style={styles.loadTitle}>
                {load.name}
              </Text>

              <View style={styles.graphContainer}>
                <PowerGraph 
                  data={loadGraphs[load.load_id] ?? []}
                />
              </View>
            </View>
          ))}
        </View>

        {Platform.OS === 'web' && <WebBadge />}

      </ThemedView>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scrollView: {
    flex: 1,
  },
  contentContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
  },
  container: {
    maxWidth: MaxContentWidth,
    flexGrow: 1,
  },
  segmentContainer: {
    flexDirection: "row",
    backgroundColor: "#E5E7EB",
    borderRadius: 20,
    padding: 4,
    width: "90%",
    alignSelf: "center",
    marginTop: 20
  },
  segment: {
    flex: 1,
    paddingVertical: 8,
    alignItems: "center",
    borderRadius: 16,
  },
  selectedSegment: {
    backgroundColor: "#FFFFFF",
    shadowColor: "#000",
    shadowOffset: {
      width: 0,
      height: 2
    },
    shadowOpacity: 0.15,
    shadowRadius: 3,
    elevation: 3,
  },
  segmentText: {
    fontSize: 14,
    color: "#6B7280",
  },
  selectedText: {
    color: "#111827",
    fontWeight: "600",
  },
  loadsContainer: {
    width: "100%",
    alignItems: "center",
    gap: 20,
    paddingVertical: 20,
  },
  loadCard: {
    backgroundColor: "#FFFFFF",
    width: "90%",
    minHeight: 300,
    padding: 20,
    borderRadius: 15,
    shadowColor: "#000",
    shadowOffset: {
      width: 0,
      height: 3
    },
    shadowOpacity: 0.15,
    shadowRadius: 5,
    elevation: 5,
  },
  loadTitle: {
    fontSize: 22,
    fontWeight: "700",
    color: "#1F2937",
    marginBottom: 15,
  },
  graphContainer: {
    width: "100%",
    height: 220,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: 10,
    backgroundColor: "#F9FAFB",
  },
});