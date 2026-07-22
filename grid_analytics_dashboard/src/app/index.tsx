import * as Device from 'expo-device';
import { Platform, StyleSheet, View, Text, useWindowDimensions } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AnimatedIcon } from '@/components/animated-icon';
import { HintRow } from '@/components/hint-row';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { WebBadge } from '@/components/web-badge';
import { BottomTabInset, MaxContentWidth, Spacing } from '@/constants/theme';

import React, { useEffect, useState } from "react";
import { getCurrentStatus, getCurrentPower, getCurrentEnergy, getWeather, getLoadsData } from "../api/api.js"

function getDevMenuHint() {
  if (Platform.OS === 'web') {
    return <ThemedText type="small">use browser devtools</ThemedText>;
  }
  if (Device.isDevice) {
    return (
      <ThemedText type="small">
        shake device or press <ThemedText type="code">m</ThemedText> in terminal
      </ThemedText>
    );
  }
  const shortcut = Platform.OS === 'android' ? 'cmd+m (or ctrl+m)' : 'cmd+d';
  return (
    <ThemedText type="small">
      press <ThemedText type="code">{shortcut}</ThemedText>
    </ThemedText>
  );
}

type Weather = {
  temperature: number,
  humidity: number,
  windspeed: number,
  rainfall: number
}

type Load = {
  name: string;
  connected: boolean;
  power: number;
}

export default function HomeScreen() {
  const { width } = useWindowDimensions();

  const isLargeScreen = width > 768;

  const [status, setStatus] = useState<string | null>(null);
  const [power, setPower] = useState<string | null>(null);
  const [energy, setEnergy] = useState<string | null>(null);
  const [weather, setWeather] = useState<Weather | null>(null);
  const [loads, setLoads] = useState<Load[]>([]);

  useEffect(() => {
    async function systemStatus(){
      const status_res = await getCurrentStatus();
      setStatus(status_res)

      const power_res = await getCurrentPower();
      setPower(power_res)

      const energy_res = await getCurrentEnergy();
      setEnergy(energy_res)

      const loads_res = await getLoadsData();
      setLoads(loads_res)
    }

    systemStatus();

    const interval = setInterval(() => {
      systemStatus();
    }, 1000);

    return () => clearInterval(interval);
  }, [])

  useEffect(() => {
    async function updateWeather(){
      const weather_features = await getWeather();
      setWeather(weather_features)

    }

    updateWeather();

    const interval = setInterval(() => {
      updateWeather();
    }, 900000);

    return () => clearInterval(interval)
  }, [])

  const getStatusColor = () => {
    switch(status){
      case "Normal":
        return '#22C55E'

      case "Warning":
        return '#FACC15'

      case "Recovering":
        return '#F9A8D4'

      case "Critical":
        return '#EF4444'

      case "Islanded":
        return '#3B82F6'

      default: 
        return "#9CA3AF"
    }
  }

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea}>
        <View 
          style={[
            styles.cardContainer,
            {
              flexDirection: isLargeScreen ? "row" : "column",
              marginTop: 75
            }
          ]}
        >

          <View 
            style={[
              styles.card,
              {
                width: isLargeScreen ? "45%" : "90%"
              }
            ]}
          >
          
            <Text style={styles.card_title}>
              System Overview
            </Text>

            <View style={styles.row}>
              <Text style={styles.label}>
                Status:
              </Text>

              <View style={styles.statusContainer}>
                <View 
                  style={[
                    styles.statusCircle, 
                    {
                      backgroundColor: getStatusColor()
                    }
                  ]}
                />

                <Text style={styles.value}>
                  {status ?? "Loading..."}
                </Text>
              </View>
            </View>

            <View style={styles.row}>
              <Text style={styles.label}>
                Current Power Usage:
              </Text>

              <Text style={styles.value}>
                {power ?? "Loading..."}
              </Text>
            </View>

            <View style={styles.row}>
              <Text style={styles.label}>
                Current Energy Usage:
              </Text>

              <Text style={styles.value}>
                {energy ?? "Loading..."}
              </Text>
            </View>
          </View>

          <View 
            style={[
              styles.card,
              {
                width: isLargeScreen ? "45%" : "90%"
              }
            ]}
          >

            <Text style={styles.card_title}>
              Weather
            </Text>

            <View style={styles.row}>
              <Text style={styles.label}>
                Temperature:
              </Text>

              <Text style={styles.value}>
                {weather?.temperature ?? "Loading..."}
              </Text>
            </View>

            <View style={styles.row}>
              <Text style={styles.label}>
                Humidity:
              </Text>

              <Text style={styles.value}>
                {weather?.humidity ?? "Loading..."}
              </Text>
            </View>

            <View style={styles.row}>
              <Text style={styles.label}>
                Wind Speed:
              </Text>

              <Text style={styles.value}>
                {weather?.windspeed ?? "Loading..."}
              </Text>
            </View>

            <View style={styles.row}>
              <Text style={styles.label}>
                Rainfall:
              </Text>

              <Text style={styles.value}>
                {weather?.rainfall ?? "Loading..."}
              </Text>
            </View>
          </View>
        </View>

        <View style={styles.loadsSection}>
          <Text style={styles.sectionTitle}>
            Loads
          </Text>

          <View style={styles.loadsContainer}>
            {loads.map((load) => (
              <View 
                key={load.name} 
                style={styles.loadCard}
              >

                <Text style={styles.card_title}>
                  {load.name}
                </Text>

                <View style={styles.row}>
                  <Text style={styles.label}>
                    Status:
                  </Text>

                  <View style={styles.statusContainer}>
                    <View
                      style={[
                        styles.statusCircle,
                        {
                          backgroundColor: load.connected
                            ? "#22C55E"
                            : "#9CA3AF"
                        }
                      ]}
                    />

                    <Text style={styles.value}>
                      {load.connected
                        ? "Connected"
                        : "Disconnected"
                      }
                    </Text>
                  </View>
                </View>

                <View style={styles.row}>
                  <Text style={styles.label}>
                    Power:
                  </Text>

                  <Text style={styles.value}>
                    {load.power ?? "Loading..."}
                  </Text>
                </View>
              </View>
            ))}
          </View>
        </View>
        {Platform.OS === 'web' && <WebBadge />}
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    flexDirection: 'row',
  },
  safeArea: {
    flex: 1,
    paddingHorizontal: Spacing.four,
    alignItems: 'center',
    gap: Spacing.three,
    paddingBottom: BottomTabInset + Spacing.three,
    maxWidth: MaxContentWidth,
  },
  heroSection: {
    alignItems: 'center',
    justifyContent: 'center',
    flex: 1,
    paddingHorizontal: Spacing.four,
    gap: Spacing.four,
  },
  title: {
    textAlign: 'center',
  },
  code: {
    textTransform: 'uppercase',
  },
  stepContainer: {
    gap: Spacing.three,
    alignSelf: 'stretch',
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.four,
    borderRadius: Spacing.four,
  },
  cardContainer: {
    width: "100%",
    justifyContent: "center",
    alignItems: "center",
    gap: 20,
  },
  card: {
      backgroundColor: "#FFFFFF",
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
      marginVertical: 10
  },
  card_title: {
      fontSize: 22,
      fontWeight: "700",
      textAlign: "left",
      marginBottom: 15,
      color: "#1F2937"
  },
  row: {
      flexDirection: "row",
      justifyContent: "space-between",
      marginVertical: 6
  },
  label: {
      fontSize: 16,
      color: "#6B7280",
      fontWeight: "500"
  },
  value: {
      fontSize: 16,
      fontWeight: "600",
      color: "#111827"
  },
  statusContainer: {
    flexDirection: "row",
    alignItems: "center",
  },
  statusCircle: {
    width: 12,
    height: 12,
    borderRadius: 6,
    marginRight: 5
  },
  loadsContainer: {
    width: "100%",
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "center",
    gap: 20,
  },
  loadsSection: {
    width: "100%",
    marginTop: 30,
  },
  sectionTitle: {
    fontSize: 28,
    fontWeight: "700",
    marginBottom: 20,
    color: "#1F2937",
  },
  loadCard: {
    backgroundColor: "#FFFFFF",
    width: "30%",
    minWidth: 280,
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
    marginVertical: 10,
  },
});