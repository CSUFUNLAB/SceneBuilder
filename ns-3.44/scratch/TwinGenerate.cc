#include "ns3/command-line.h"
#include "ns3/network-scene-helper.h"
#include "ns3/nstime.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <functional>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

using namespace ns3;

double
SecondsSince(std::chrono::steady_clock::time_point startedAt)
{
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - startedAt).count();
}

void
RenderWallClockProgress(std::chrono::steady_clock::time_point startedAt, Time stop)
{
    const double elapsed = SecondsSince(startedAt);
    const double simTime = Simulator::Now().GetSeconds();
    const double stopTime = stop.GetSeconds();
    const double percent = stopTime <= 0.0 ? 0.0 : std::min(100.0, simTime / stopTime * 100.0);
    std::printf("\r\033[Kelapsed=%6.2fs | sim=%6.2f/%6.2fs | progress=%6.2f%% | events=%08llu",
                elapsed,
                simTime,
                stopTime,
                percent,
                static_cast<unsigned long long>(Simulator::GetEventCount()));
    std::fflush(stdout);
}

void
PrintWallClockProgress(const std::atomic<bool>& running,
                       std::chrono::steady_clock::time_point startedAt,
                       Time stop,
                       double intervalSeconds)
{
    RenderWallClockProgress(startedAt, stop);
    while (running.load())
    {
        std::this_thread::sleep_for(
            std::chrono::milliseconds(static_cast<int>(std::max(0.1, intervalSeconds) * 1000.0)));
        RenderWallClockProgress(startedAt, stop);
    }
}

void
PrintProgress(Time stop, Time interval)
{
    std::cout << "NS3_PROGRESS sim_time=" << Simulator::Now().GetSeconds()
              << " stop_time=" << stop.GetSeconds()
              << " events=" << Simulator::GetEventCount() << std::endl;

    Time next = Simulator::Now() + interval;
    if (next < stop)
    {
        Simulator::Schedule(interval, &PrintProgress, stop, interval);
    }
}

int
main(int argc, char* argv[])
{
    std::string scene;
    std::string events;
    std::string result;
    double stopTime = 0.0;
    double progressInterval = 0.0;
    double wallProgressInterval = 5.0;
    double scaleFactor = 10.0;

    CommandLine cmd(__FILE__);
    cmd.AddValue("scene", "Scene directory path or scene name under ./scenes", scene);
    cmd.AddValue("events", "Event JSONL file. Empty means events are not loaded.", events);
    cmd.AddValue("result", "Result JSONL output path. Empty means <scene>/twin/0.jsonl.", result);
    cmd.AddValue("stopTime", "Simulation stop time in seconds. 0 means scene duration.", stopTime);
    cmd.AddValue("progressInterval",
                 "Progress report interval in simulated seconds. 0 disables machine-readable progress.",
                 progressInterval);
    cmd.AddValue("wallProgressInterval",
                 "Wall-clock progress refresh interval in seconds. 0 disables the live timer.",
                 wallProgressInterval);
    cmd.AddValue("scaleFactor",
                 "Divide channel bandwidth and traffic demand by this factor. Values below 1 use 1.",
                 scaleFactor);
    cmd.Parse(argc, argv);

    if (scene.empty())
    {
        std::cerr << "Missing required argument: --scene=<scene directory>" << std::endl;
        return 1;
    }

    NetworkSceneHelper helper;
    helper.SetSceneDirectory(scene);
    if (!events.empty())
    {
        helper.SetEventFile(events);
    }
    if (!result.empty())
    {
        helper.SetResultPath(result);
    }
    helper.SetValueScaleFactor(scaleFactor);
    if (stopTime > 0.0)
    {
        helper.SetApplicationStopTime(Seconds(stopTime));
    }
    helper.Install();

    Time stop = stopTime > 0.0 ? Seconds(stopTime) : helper.GetSceneDuration();
    Simulator::Stop(stop);
    if (progressInterval > 0.0)
    {
        Time interval = Seconds(std::max(0.001, progressInterval));
        Simulator::Schedule(Seconds(0.0), &PrintProgress, stop, interval);
    }

    std::cout << "Loaded scene: " << scene << "\n"
              << "Nodes: " << helper.GetNodeCount() << "\n"
              << "Channels: " << helper.GetChannelCount() << "\n"
              << "Flows: " << helper.GetFlowCount() << "\n"
              << "Events: " << (events.empty() ? "disabled" : events) << "\n"
              << "Scale factor: " << std::max(1.0, scaleFactor) << "\n"
              << "Stop: " << stop.GetSeconds() << "s" << std::endl;

    std::atomic<bool> running{true};
    auto startedAt = std::chrono::steady_clock::now();
    std::thread progressThread;
    if (wallProgressInterval > 0.0)
    {
        progressThread = std::thread(PrintWallClockProgress,
                                     std::cref(running),
                                     startedAt,
                                     stop,
                                     wallProgressInterval);
    }

    Simulator::Run();
    running.store(false);
    if (progressThread.joinable())
    {
        progressThread.join();
    }
    RenderWallClockProgress(startedAt, stop);
    std::printf("\n");
    std::fflush(stdout);

    helper.WriteResults();
    Simulator::Destroy();

    std::cout << "Result written"
              << (result.empty() ? " to " + scene + "/twin/0.jsonl" : " to " + result)
              << std::endl;
    return 0;
}
