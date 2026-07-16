#include "ns3/command-line.h"
#include "ns3/log.h"
#include "ns3/network-scene-helper.h"
#include "ns3/nstime.h"
#include "ns3/simulator.h"
#include "ns3/string.h"

#include <iostream>
#include <string>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("NetworkSceneRunner");

int
main(int argc, char* argv[])
{
    std::string scene = "scenes/example_id0001_Abvt_t300s";
    double stopTime = 0.0;

    CommandLine cmd(__FILE__);
    cmd.AddValue("scene", "Scene directory or scene name under ./scenes", scene);
    cmd.AddValue("stopTime", "Simulation stop time in seconds. 0 means scene duration.", stopTime);
    cmd.Parse(argc, argv);

    NetworkSceneHelper helper;
    helper.SetSceneDirectory(scene);
    if (stopTime > 0.0)
    {
        helper.SetApplicationStopTime(Seconds(stopTime));
    }
    helper.Install();

    Time stop = stopTime > 0.0 ? Seconds(stopTime) : helper.GetSceneDuration();
    Simulator::Stop(stop);

    std::cout << "Loaded scene: " << scene << "\n"
              << "Nodes: " << helper.GetNodeCount() << "\n"
              << "Channels: " << helper.GetChannelCount() << "\n"
              << "Flows: " << helper.GetFlowCount() << "\n"
              << "Stop: " << stop.GetSeconds() << "s" << std::endl;

    Simulator::Run();
    helper.WriteResults();
    Simulator::Destroy();
    return 0;
}
