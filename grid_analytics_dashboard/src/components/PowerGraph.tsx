import React from "react";
import { View, StyleSheet } from "react-native";

type DataPoint = {
  time: string;
  power: number;
};

type Props = {
  data: DataPoint[];
};

export default function PowerGraph({ data }: Props) {

  if (!data || data.length === 0) {
    return (
      <View style={styles.empty}>
      </View>
    );
  }

  const width = 300;
  const height = 180;

  const powers = data.map((point) => point.power);

  const minPower = Math.min(...powers);
  const maxPower = Math.max(...powers);

  const points = data.map((point, index) => {
    const x = (index / (data.length - 1)) * width;

    const y =
      height -
      ((point.power - minPower) /
        (maxPower - minPower || 1)) *
        height;

    return {
      x,
      y
    };
  });


  return (
    <View style={[styles.graph, {width, height}]}>

      {points.map((point, index) => {

        if (index === points.length - 1) {
          return null;
        }

        const next = points[index + 1];

        const dx = next.x - point.x;
        const dy = next.y - point.y;

        const length = Math.sqrt(
          dx * dx + dy * dy
        );

        const angle = Math.atan2(dy, dx) * 180 / Math.PI;


        return (
          <View
            key={index}
            style={[
              styles.line,
              {
                width: length,
                left: point.x,
                top: point.y,
                transform: [
                  {
                    rotate: `${angle}deg`
                  }
                ]
              }
            ]}
          />
        );
      })}


      {points.map((point,index)=>(
        <View
          key={index}
          style={[
            styles.dot,
            {
              left: point.x-3,
              top: point.y-3
            }
          ]}
        />
      ))}

    </View>
  );
}


const styles = StyleSheet.create({

  graph:{
    backgroundColor:"#F9FAFB",
    overflow:"hidden",
  },

  line:{
    height:2,
    backgroundColor:"#2563EB",
    position:"absolute",
    transformOrigin:"left center"
  },

  dot:{
    width:6,
    height:6,
    borderRadius:3,
    backgroundColor:"#2563EB",
    position:"absolute"
  },

  empty:{
    height:180,
    justifyContent:"center",
    alignItems:"center"
  }

});