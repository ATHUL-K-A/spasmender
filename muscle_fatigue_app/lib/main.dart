import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:http/http.dart' as http;

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return const MaterialApp(
      debugShowCheckedModeBanner: false,
      home: MainScreen(),
    );
  }
}

class MainScreen extends StatelessWidget {
  const MainScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text("Muscle Fatigue Monitor"),
      ),
      body: const ReadFatigueScreen(),
    );
  }
}

class ReadFatigueScreen extends StatefulWidget {
  const ReadFatigueScreen({super.key});

  @override
  State<ReadFatigueScreen> createState() => _ReadFatigueScreenState();
}

class _ReadFatigueScreenState extends State<ReadFatigueScreen> {
  final String baseUrl = "http://192.168.1.167:5000";

  Timer? timer;
  bool monitoring = false;
  String statusText = "STOPPED";

  int selectedSensor = 0;

  List<FlSpot> rmsSpots = [];
  List<FlSpot> mdfSpots = [];

  double timeIndex = 0;

  // ---------------- SENSOR SWITCH ----------------
  Future<void> setSensor(int sensorIndex) async {
    try {
      await http.get(Uri.parse("$baseUrl/set_channel/$sensorIndex"));
    } catch (e) {
      print("Error setting sensor: $e");
    }
  }

  // ---------------- START ----------------
  Future<void> startMonitoring() async {
    await http.get(Uri.parse("$baseUrl/start"));

    setState(() {
      monitoring = true;
      rmsSpots.clear();
      mdfSpots.clear();
      timeIndex = 0;
    });

    timer = Timer.periodic(const Duration(seconds: 1), (_) async {
      final response = await http.get(Uri.parse("$baseUrl/status"));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);

        double rms = (data["live_rms"] ?? 0).toDouble();
        double mdf = (data["live_mdf"] ?? 0).toDouble();
        bool fatigueDetected = data["fatigue_detected"] ?? false;
        String backendStatus = data["status"] ?? "UNKNOWN";

        setState(() {
          rmsSpots.add(FlSpot(timeIndex, rms));
          mdfSpots.add(FlSpot(timeIndex, mdf));
          statusText = backendStatus;
          timeIndex += 1;
        });

        if (fatigueDetected) {
          stopMonitoring();

          showDialog(
            context: context,
            builder: (_) => const AlertDialog(
              title: Text("⚠️ FATIGUE DETECTED"),
              content: Text("Sustained fatigue detected."),
            ),
          );
        }
      }
    });
  }

  // ---------------- STOP ----------------
  Future<void> stopMonitoring() async {
    await http.get(Uri.parse("$baseUrl/stop"));
    timer?.cancel();

    setState(() {
      monitoring = false;
    });
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  // ---------------- GRAPH ----------------
  Widget buildGraph(
      String title, List<FlSpot> spots, Color color, double maxY) {
    return Expanded(
      child: Column(
        children: [
          Text(title,
              style:
                  const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          Expanded(
            child: spots.isEmpty
                ? const Center(child: Text("No data yet..."))
                : LineChart(
                    LineChartData(
                      minX: 0,
                      maxX: timeIndex < 10 ? 10 : timeIndex + 5,
                      minY: 0,
                      maxY: maxY,
                      lineBarsData: [
                        LineChartBarData(
                          spots: spots,
                          isCurved: true,
                          color: color,
                          dotData: const FlDotData(show: false),
                        ),
                      ],
                    ),
                  ),
          ),
        ],
      ),
    );
  }

  // ---------------- UI ----------------
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        children: [
          Text("Status: $statusText",
              style:
                  const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),

          const SizedBox(height: 10),

          if (monitoring) const CircularProgressIndicator(),

          const SizedBox(height: 20),

          // 🔽 SENSOR DROPDOWN
          DropdownButton<int>(
            value: selectedSensor,
            items: const [
              DropdownMenuItem(value: 0, child: Text("Sensor 1")),
              DropdownMenuItem(value: 1, child: Text("Sensor 2")),
              DropdownMenuItem(value: 2, child: Text("Sensor 3")),
              DropdownMenuItem(value: 3, child: Text("Sensor 4")),
            ],
            onChanged: (value) {
              if (value != null) {
                setState(() {
                  selectedSensor = value;

                  // reset graph when switching sensor
                  rmsSpots.clear();
                  mdfSpots.clear();
                  timeIndex = 0;
                });

                setSensor(value);
              }
            },
          ),

          const SizedBox(height: 20),

          ElevatedButton(
            onPressed: monitoring ? null : startMonitoring,
            child: const Text("Start"),
          ),

          const SizedBox(height: 10),

          ElevatedButton(
            onPressed: monitoring ? stopMonitoring : null,
            child: const Text("Stop"),
          ),

          const SizedBox(height: 20),

          buildGraph("Live RMS", rmsSpots, Colors.blue, 700),

          const SizedBox(height: 10),

          buildGraph("Live MDF (Hz)", mdfSpots, Colors.orange, 100),
        ],
      ),
    );
  }
}

//////////////////////////////////////////////////////////////
// PAST RECORDS SCREEN
//////////////////////////////////////////////////////////////

// class PastRecordsScreen extends StatefulWidget {
//   const PastRecordsScreen({super.key});

//   @override
//   State<PastRecordsScreen> createState() => _PastRecordsScreenState();
// }

// class _PastRecordsScreenState extends State<PastRecordsScreen> {
//   String selectedSensor = "Sensor 1";

//   List<FlSpot> rawSpots = [];
//   List<FlSpot> envSpots = [];

//   double avgRaw = 0;
//   double avgEnv = 0;
//   double maxRaw = 0;
//   double maxEnv = 0;
//   double duration = 0;

//   Future<void> loadSensorData() async {
//     final file = await getSensorFile(selectedSensor);
//     if (!(await file.exists())) return;

//     final lines = await file.readAsLines();
//     if (lines.length <= 1) return;

//     rawSpots.clear();
//     envSpots.clear();

//     double sumRaw = 0;
//     double sumEnv = 0;
//     maxRaw = 0;
//     maxEnv = 0;

//     for (int i = 1; i < lines.length; i++) {
//       final parts = lines[i].split(',');
//       if (parts.length != 3) continue;

//       double time = double.tryParse(parts[0]) ?? 0;
//       double raw = double.tryParse(parts[1]) ?? 0;
//       double env = double.tryParse(parts[2]) ?? 0;

//       rawSpots.add(FlSpot(time, raw));
//       envSpots.add(FlSpot(time, env));

//       sumRaw += raw;
//       sumEnv += env;

//       if (raw > maxRaw) maxRaw = raw;
//       if (env > maxEnv) maxEnv = env;

//       duration = time;
//     }

//     avgRaw = rawSpots.isNotEmpty ? sumRaw / rawSpots.length : 0;
//     avgEnv = envSpots.isNotEmpty ? sumEnv / envSpots.length : 0;

//     setState(() {});
//   }

//   @override
//   void initState() {
//     super.initState();
//     loadSensorData();
//   }

//   @override
//   Widget build(BuildContext context) {
//     return Padding(
//       padding: const EdgeInsets.all(16),
//       child: Column(
//         children: [
//           DropdownButtonFormField(
//             value: selectedSensor,
//             items: ["Sensor 1", "Sensor 2", "Sensor 3", "Sensor 4"]
//                 .map((sensor) =>
//                     DropdownMenuItem(value: sensor, child: Text(sensor)))
//                 .toList(),
//             onChanged: (value) {
//               selectedSensor = value!;
//               loadSensorData();
//             },
//             decoration:
//                 const InputDecoration(border: OutlineInputBorder()),
//           ),
//           const SizedBox(height: 20),
//           Expanded(
//             child: LineChart(
//               LineChartData(
//                 lineBarsData: [
//                   LineChartBarData(
//                     spots: rawSpots,
//                     color: Colors.blue,
//                     isCurved: true,
//                     dotData: const FlDotData(show: false),
//                   ),
//                   LineChartBarData(
//                     spots: envSpots,
//                     color: Colors.orange,
//                     isCurved: true,
//                     dotData: const FlDotData(show: false),
//                   ),
//                 ],
//               ),
//             ),
//           ),
//           const SizedBox(height: 10),
//           Text("Duration: ${duration.toStringAsFixed(2)} s"),
//           Text("Avg Raw: ${avgRaw.toStringAsFixed(3)}"),
//           Text("Avg Env: ${avgEnv.toStringAsFixed(3)}"),
//           Text("Max Raw: ${maxRaw.toStringAsFixed(3)}"),
//           Text("Max Env: ${maxEnv.toStringAsFixed(3)}"),
//         ],
//       ),
//     );
//   }
// }
