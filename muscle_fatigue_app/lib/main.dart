import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:path_provider/path_provider.dart';
import 'package:http/http.dart' as http;

void main() {
  runApp(const MyApp());
}

//////////////////////////////////////////////////////////////
// FILE UTILITIES
//////////////////////////////////////////////////////////////

Future<File> getSensorFile(String sensor) async {
  final dir = await getApplicationDocumentsDirectory();
  final fileName = sensor.replaceAll(" ", "_").toLowerCase();
  return File("${dir.path}/$fileName.csv");
}

Future<void> appendToCSV(
    String sensor, double time, double raw, double env) async {
  final file = await getSensorFile(sensor);

  if (!(await file.exists())) {
    await file.writeAsString("time,raw,env\n");
  }

  await file.writeAsString("$time,$raw,$env\n", mode: FileMode.append);
}

//////////////////////////////////////////////////////////////
// MAIN APP
//////////////////////////////////////////////////////////////

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        scaffoldBackgroundColor: Colors.blue.shade50,
        colorSchemeSeed: Colors.blue,
      ),
      home: const MainScreen(),
    );
  }
}

//////////////////////////////////////////////////////////////
// MAIN SCREEN
//////////////////////////////////////////////////////////////

class MainScreen extends StatelessWidget {
  const MainScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text("Muscle Fatigue Monitor"),
          bottom: const TabBar(
            tabs: [
              Tab(text: "Read Fatigue"),
              Tab(text: "Check Past Records"),
            ],
          ),
        ),
        body: const TabBarView(
          children: [ReadFatigueScreen(), PastRecordsScreen()],
        ),
      ),
    );
  }
}

//////////////////////////////////////////////////////////////
// READ FATIGUE SCREEN
//////////////////////////////////////////////////////////////

class ReadFatigueScreen extends StatefulWidget {
  const ReadFatigueScreen({super.key});

  @override
  State<ReadFatigueScreen> createState() => _ReadFatigueScreenState();
}

class _ReadFatigueScreenState extends State<ReadFatigueScreen> {
  Timer? statusTimer;
  bool isRunning = false;
  String statusText = "Idle";
  String selectedSensor = "Sensor 1";

  final String baseUrl = "http://192.168.1.167:5000";
 //final String baseUrl = "http://192.168.11.4:5000";
  Future<void> startTest() async {
    await http.get(Uri.parse("$baseUrl/start"));

    setState(() {
      isRunning = true;
      statusText = "Monitoring...";
    });

    statusTimer = Timer.periodic(const Duration(seconds: 1), (timer) async {
      final response = await http.get(Uri.parse("$baseUrl/status"));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);

        if (data["fatigue"] == true) {
          timer.cancel();
          await stopTest();

          setState(() {
            statusText = "⚠️ FATIGUE DETECTED";
          });

          showDialog(
            context: context,
            builder: (_) => const AlertDialog(
              title: Text("⚠️ Fatigue Detected"),
              content:
                  Text("Muscle fatigue detected.\nTest stopped automatically."),
            ),
          );
        }
      }
    });
  }

  Future<void> stopTest() async {
    await http.get(Uri.parse("$baseUrl/stop"));

    statusTimer?.cancel();

    setState(() {
      isRunning = false;
      if (statusText != "⚠️ FATIGUE DETECTED") {
        statusText = "Stopped";
      }
    });
  }

  @override
  void dispose() {
    statusTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        children: [
          DropdownButtonFormField(
            value: selectedSensor,
            items: ["Sensor 1", "Sensor 2", "Sensor 3", "Sensor 4"]
                .map((sensor) =>
                    DropdownMenuItem(value: sensor, child: Text(sensor)))
                .toList(),
            onChanged: (value) {
              selectedSensor = value!;
            },
            decoration:
                const InputDecoration(border: OutlineInputBorder()),
          ),
          const SizedBox(height: 40),
          Text(
            statusText,
            style: const TextStyle(
                fontSize: 18, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 20),
          if (isRunning) const CircularProgressIndicator(),
          const SizedBox(height: 40),
          ElevatedButton(
              onPressed: isRunning ? null : startTest,
              child: const Text("Start Test")),
          const SizedBox(height: 20),
          ElevatedButton(
              onPressed: isRunning ? stopTest : null,
              child: const Text("Stop Test")),
        ],
      ),
    );
  }
}

//////////////////////////////////////////////////////////////
// PAST RECORDS SCREEN
//////////////////////////////////////////////////////////////

class PastRecordsScreen extends StatefulWidget {
  const PastRecordsScreen({super.key});

  @override
  State<PastRecordsScreen> createState() => _PastRecordsScreenState();
}

class _PastRecordsScreenState extends State<PastRecordsScreen> {
  String selectedSensor = "Sensor 1";

  List<FlSpot> rawSpots = [];
  List<FlSpot> envSpots = [];

  double avgRaw = 0;
  double avgEnv = 0;
  double maxRaw = 0;
  double maxEnv = 0;
  double duration = 0;

  Future<void> loadSensorData() async {
    final file = await getSensorFile(selectedSensor);
    if (!(await file.exists())) return;

    final lines = await file.readAsLines();
    if (lines.length <= 1) return;

    rawSpots.clear();
    envSpots.clear();

    double sumRaw = 0;
    double sumEnv = 0;
    maxRaw = 0;
    maxEnv = 0;

    for (int i = 1; i < lines.length; i++) {
      final parts = lines[i].split(',');
      if (parts.length != 3) continue;

      double time = double.tryParse(parts[0]) ?? 0;
      double raw = double.tryParse(parts[1]) ?? 0;
      double env = double.tryParse(parts[2]) ?? 0;

      rawSpots.add(FlSpot(time, raw));
      envSpots.add(FlSpot(time, env));

      sumRaw += raw;
      sumEnv += env;

      if (raw > maxRaw) maxRaw = raw;
      if (env > maxEnv) maxEnv = env;

      duration = time;
    }

    avgRaw = rawSpots.isNotEmpty ? sumRaw / rawSpots.length : 0;
    avgEnv = envSpots.isNotEmpty ? sumEnv / envSpots.length : 0;

    setState(() {});
  }

  @override
  void initState() {
    super.initState();
    loadSensorData();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        children: [
          DropdownButtonFormField(
            value: selectedSensor,
            items: ["Sensor 1", "Sensor 2", "Sensor 3", "Sensor 4"]
                .map((sensor) =>
                    DropdownMenuItem(value: sensor, child: Text(sensor)))
                .toList(),
            onChanged: (value) {
              selectedSensor = value!;
              loadSensorData();
            },
            decoration:
                const InputDecoration(border: OutlineInputBorder()),
          ),
          const SizedBox(height: 20),
          Expanded(
            child: LineChart(
              LineChartData(
                lineBarsData: [
                  LineChartBarData(
                    spots: rawSpots,
                    color: Colors.blue,
                    isCurved: true,
                    dotData: const FlDotData(show: false),
                  ),
                  LineChartBarData(
                    spots: envSpots,
                    color: Colors.orange,
                    isCurved: true,
                    dotData: const FlDotData(show: false),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 10),
          Text("Duration: ${duration.toStringAsFixed(2)} s"),
          Text("Avg Raw: ${avgRaw.toStringAsFixed(3)}"),
          Text("Avg Env: ${avgEnv.toStringAsFixed(3)}"),
          Text("Max Raw: ${maxRaw.toStringAsFixed(3)}"),
          Text("Max Env: ${maxEnv.toStringAsFixed(3)}"),
        ],
      ),
    );
  }
}
