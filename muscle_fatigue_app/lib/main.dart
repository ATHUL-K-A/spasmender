// =============================================================
// Muscle Fatigue Monitor — Flutter Mobile Application
// Connects to Flask backend via REST + WebSocket (Socket.IO)
// Displays live RMS, MDF, fatigue counter, and SVM confidence
// =============================================================

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:http/http.dart' as http;
import 'package:socket_io_client/socket_io_client.dart' as IO;

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.indigo),
        useMaterial3: true,
      ),
      home: const MainScreen(),
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
        centerTitle: true,
        backgroundColor: Colors.indigo,
        foregroundColor: Colors.white,
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
  // --- Update this to your Flask machine's IP ---
  final String baseUrl   = "http://127.0.0.1:5000";
  final String socketUrl = "http://127.0.0.1:5000";

  IO.Socket? socket;
  bool   monitoring      = false;
  bool   socketConnected = false;
  String statusText      = "STOPPED";
  int    selectedSensor  = 0;

  // --- Live metric values ---
  List<FlSpot> rmsSpots = [];
  List<FlSpot> mdfSpots = [];
  double statusTimeIndex    = 0;
  double fatigueConfidence  = 0;
  double liveRms            = 0;
  double liveMdf            = 0;
  int    fatigueCounter     = 0;

  // ----------------------------------------------------------------
  // SOCKET SETUP
  // ----------------------------------------------------------------
  void connectSocket() {
    socket = IO.io(socketUrl, <String, dynamic>{
      "transports": ["websocket"],
      "autoConnect": true,
    });

    socket!.onConnect((_) {
      setState(() => socketConnected = true);
      print("Socket connected");
    });

    socket!.onDisconnect((_) {
      setState(() => socketConnected = false);
      print("Socket disconnected");
    });

    // graph_history — seeds RMS/MDF graphs when app connects mid-session
    socket!.on("graph_history", (data) {
      if (data is! List) return;
      // graph_history carries raw/env samples — we can't replot
      // RMS/MDF from them without recomputing, so we just note
      // that a session is already running and wait for next status_update
      print("Graph history received: ${data.length} samples (session already running)");
    });

    // status_update — fired by Flask on every polled batch
    socket!.on("status_update", (data) {
      final double rms      = (data["live_rms"]           as num).toDouble();
      final double mdf      = (data["live_mdf"]           as num).toDouble();
      final int    fc       = (data["fatigue_counter"]    as num).toInt();
      final double conf     = (data["fatigue_confidence"] as num? ?? 0).toDouble();
      final String status   =  data["status"]             ?? "UNKNOWN";
      final bool   running  =  status == "RUNNING" || status == "FATIGUED";

      setState(() {
        liveRms           = rms;
        liveMdf           = mdf;
        fatigueCounter    = fc;
        fatigueConfidence = conf;
        statusText        = status;
        monitoring        = running;

        if (rms > 0 || mdf > 0) {
          rmsSpots.add(FlSpot(statusTimeIndex, rms));
          mdfSpots.add(FlSpot(statusTimeIndex, mdf));
          statusTimeIndex += 1;
        }
      });
    });

    // fatigue_alert — fired once when fatigue threshold is crossed
    socket!.on("fatigue_alert", (_) {
      stopMonitoring();
      if (!mounted) return;
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (_) => AlertDialog(
          icon: const Icon(Icons.warning_amber_rounded,
              color: Colors.red, size: 48),
          title: const Text("⚠️ Fatigue Detected",
              textAlign: TextAlign.center),
          content: const Text(
            "Sustained muscle fatigue has been detected.\nPlease rest.",
            textAlign: TextAlign.center,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text("OK"),
            ),
          ],
        ),
      );
    });

    socket!.connect();
  }

  // ----------------------------------------------------------------
  // START / STOP
  // ----------------------------------------------------------------
  Future<void> startMonitoring() async {
    try {
      final res = await http.get(Uri.parse("$baseUrl/start"));
      if (res.statusCode == 200) {
        setState(() {
          monitoring        = true;
          rmsSpots.clear();
          mdfSpots.clear();
          statusTimeIndex   = 0;
          liveRms           = 0;
          liveMdf           = 0;
          fatigueCounter    = 0;
          fatigueConfidence = 0;   // reset confidence on new session
          statusText        = "RUNNING";
        });
      } else {
        // Show error if ESP32 not reachable (503)
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text("Failed to start: ${res.statusCode} — check ESP32 connection"),
              backgroundColor: Colors.red,
            ),
          );
        }
      }
    } catch (e) {
      print("Start error: $e");
    }
  }

  Future<void> stopMonitoring() async {
    try {
      await http.get(Uri.parse("$baseUrl/stop"));
    } catch (e) {
      print("Stop error: $e");
    }
    setState(() {
      monitoring = false;
      statusText = "STOPPED";
    });
  }

  // ----------------------------------------------------------------
  // CHANNEL SWITCH
  // ----------------------------------------------------------------
  Future<void> setSensor(int ch) async {
    try {
      await http.get(Uri.parse("$baseUrl/set_channel/$ch"));
      setState(() {
        // Reset graphs on channel switch — different muscle, different signal
        rmsSpots.clear();
        mdfSpots.clear();
        statusTimeIndex   = 0;
        fatigueConfidence = 0;
      });
    } catch (e) {
      print("Set sensor error: $e");
    }
  }

  // ----------------------------------------------------------------
  // LIFECYCLE
  // ----------------------------------------------------------------
  @override
  void initState() {
    super.initState();
    connectSocket();
  }

  @override
  void dispose() {
    socket?.dispose();
    super.dispose();
  }

  // ----------------------------------------------------------------
  // GRAPH BUILDERS
  // ----------------------------------------------------------------
  Widget buildRMSGraph() {
    return _graphCard(
      title:     "RMS over Time",
      color:     Colors.blue,
      spots:     rmsSpots,
      minX:      0,
      maxX:      statusTimeIndex < 10 ? 10 : statusTimeIndex + 5,
      minY:      0,
      maxY:      900,
      yInterval: 100,
    );
  }

  Widget buildMDFGraph() {
    return _graphCard(
      title:     "MDF (Hz) over Time",
      color:     Colors.orange,
      spots:     mdfSpots,
      minX:      0,
      maxX:      statusTimeIndex < 10 ? 10 : statusTimeIndex + 5,
      minY:      0,
      maxY:      100,
      yInterval: 10,
    );
  }

  Widget _graphCard({
    required String       title,
    required Color        color,
    required List<FlSpot> spots,
    required double       minX,
    required double       maxX,
    required double       minY,
    required double       maxY,
    required double       yInterval,
  }) {
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title,
                style: const TextStyle(
                    fontSize: 14, fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            SizedBox(
              height: 180,
              child: spots.isEmpty
                  ? const Center(
                      child: Text("Waiting for data...",
                          style: TextStyle(color: Colors.grey)))
                  : LineChart(
                      LineChartData(
                        minX: minX,
                        maxX: maxX,
                        minY: minY,
                        maxY: maxY,
                        gridData: FlGridData(
                          show: true,
                          horizontalInterval: yInterval,
                          drawVerticalLine: false,
                          getDrawingHorizontalLine: (_) => FlLine(
                            color: Colors.grey.withOpacity(0.25),
                            strokeWidth: 1,
                          ),
                        ),
                        borderData: FlBorderData(
                          show: true,
                          border: Border(
                            left:   BorderSide(color: Colors.grey.shade400),
                            bottom: BorderSide(color: Colors.grey.shade400),
                          ),
                        ),
                        titlesData: FlTitlesData(
                          topTitles:    const AxisTitles(
                              sideTitles: SideTitles(showTitles: false)),
                          rightTitles:  const AxisTitles(
                              sideTitles: SideTitles(showTitles: false)),
                          bottomTitles: const AxisTitles(
                              sideTitles: SideTitles(showTitles: false)),
                          leftTitles: AxisTitles(
                            sideTitles: SideTitles(
                              showTitles:   true,
                              interval:     yInterval,
                              reservedSize: 36,
                              getTitlesWidget: (value, meta) {
                                if (value < minY || value > maxY) {
                                  return const SizedBox.shrink();
                                }
                                return Text(
                                  value.toInt().toString(),
                                  style: TextStyle(
                                    fontSize: 10,
                                    color: Colors.grey.shade600,
                                  ),
                                );
                              },
                            ),
                          ),
                        ),
                        lineBarsData: [
                          LineChartBarData(
                            spots:    spots,
                            isCurved: false,
                            color:    color,
                            barWidth: 1.5,
                            dotData:  const FlDotData(show: false),
                            belowBarData: BarAreaData(
                              show:  true,
                              color: color.withOpacity(0.08),
                            ),
                          ),
                        ],
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  // ----------------------------------------------------------------
  // STATUS CHIP COLOR
  // ----------------------------------------------------------------
  Color get statusColor {
    switch (statusText) {
      case "RUNNING":  return Colors.green;
      case "FATIGUED": return Colors.orange;
      default:         return Colors.grey;
    }
  }

  // ----------------------------------------------------------------
  // CONFIDENCE PROGRESS BAR
  // Shows SVM fatigue probability as a colour-coded bar
  // green < 40%, orange 40–69%, red >= 70%
  // ----------------------------------------------------------------
  Widget buildConfidenceBar() {
    final Color barColor = fatigueConfidence >= 0.7
        ? Colors.red
        : fatigueConfidence >= 0.4
            ? Colors.orange
            : Colors.green;

    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text("Fatigue Confidence",
                    style: TextStyle(
                        fontSize: 13, fontWeight: FontWeight.bold)),
                Text(
                  "${(fatigueConfidence * 100).toStringAsFixed(0)}%",
                  style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.bold,
                      color: barColor),
                ),
              ],
            ),
            const SizedBox(height: 8),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value:            fatigueConfidence,
                minHeight:        12,
                backgroundColor:  Colors.grey.shade200,
                valueColor:       AlwaysStoppedAnimation<Color>(barColor),
              ),
            ),
            const SizedBox(height: 4),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text("0%",   style: TextStyle(fontSize: 10, color: Colors.grey.shade500)),
                Text("70% threshold",
                    style: TextStyle(fontSize: 10, color: Colors.grey.shade500)),
                Text("100%", style: TextStyle(fontSize: 10, color: Colors.grey.shade500)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  // ----------------------------------------------------------------
  // METRIC CARD
  // ----------------------------------------------------------------
  Widget _metricCard(String label, String value, Color color) {
    return Expanded(
      child: Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 6),
          child: Column(
            children: [
              Text(label,
                  style: TextStyle(
                      fontSize: 11,
                      color: color,
                      fontWeight: FontWeight.bold)),
              const SizedBox(height: 4),
              Text(value,
                  style: TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.bold,
                      color: color)),
            ],
          ),
        ),
      ),
    );
  }

  // ----------------------------------------------------------------
  // BUILD
  // ----------------------------------------------------------------
  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [

          // --- Status chip + WiFi indicator ---
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Chip(
                label: Text(
                  statusText,
                  style: const TextStyle(
                      color: Colors.white, fontWeight: FontWeight.bold),
                ),
                backgroundColor: statusColor,
              ),
              Row(children: [
                Icon(
                  socketConnected ? Icons.wifi : Icons.wifi_off,
                  color: socketConnected ? Colors.green : Colors.red,
                  size: 18,
                ),
                const SizedBox(width: 4),
                Text(
                  socketConnected ? "Connected" : "Disconnected",
                  style: TextStyle(
                      color: socketConnected ? Colors.green : Colors.red,
                      fontSize: 13),
                ),
              ]),
            ],
          ),

          const SizedBox(height: 8),

          // --- Live metric cards ---
          Row(children: [
            _metricCard("RMS",       liveRms.toStringAsFixed(3),  Colors.blue),
            const SizedBox(width: 8),
            _metricCard("MDF (Hz)",  liveMdf.toStringAsFixed(1),  Colors.orange),
            const SizedBox(width: 8),
            _metricCard("Fatigue #", fatigueCounter.toString(),    Colors.red),
          ]),

          const SizedBox(height: 8),

          // --- SVM confidence progress bar ---
          buildConfidenceBar(),

          const SizedBox(height: 8),

          // --- Sensor selector ---
          Row(children: [
            const Text("Sensor: ",
                style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(width: 8),
            DropdownButton<int>(
              value: selectedSensor,
              items: List.generate(
                4,
                (i) => DropdownMenuItem(
                    value: i, child: Text("Sensor ${i + 1}")),
              ),
              onChanged: monitoring
                  ? (value) {
                      if (value != null) {
                        setState(() => selectedSensor = value);
                        setSensor(value);
                      }
                    }
                  : null,
            ),
          ]),

          const SizedBox(height: 8),

          // --- Start / Stop buttons ---
          Row(children: [
            Expanded(
              child: ElevatedButton.icon(
                onPressed: monitoring ? null : startMonitoring,
                icon:  const Icon(Icons.play_arrow),
                label: const Text("Start"),
                style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.green,
                    foregroundColor: Colors.white),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: ElevatedButton.icon(
                onPressed: monitoring ? stopMonitoring : null,
                icon:  const Icon(Icons.stop),
                label: const Text("Stop"),
                style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.red,
                    foregroundColor: Colors.white),
              ),
            ),
          ]),

          const SizedBox(height: 16),

          // --- Graphs ---
          buildRMSGraph(),
          buildMDFGraph(),

        ],
      ),
    );
  }
}