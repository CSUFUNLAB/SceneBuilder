#ifndef NETWORK_SCENE_TRAFFIC_H
#define NETWORK_SCENE_TRAFFIC_H

#include "ns3/address.h"
#include "ns3/application.h"
#include "ns3/application-container.h"
#include "ns3/nstime.h"
#include "ns3/node.h"
#include "ns3/ptr.h"

#include <cstdint>
#include <string>

namespace ns3
{

struct NetworkSceneTrafficPattern
{
    std::string id;
    std::string src;
    std::string dst;
    double demandMbps{0.0};
    std::string featureModel{"cbr"};
    double paramLambda{0.0};
    double paramOnMean{0.0};
    double paramOffMean{0.0};
    double paramPeakRateMbps{0.0};
};

ApplicationContainer InstallNetworkSceneTrafficSource(Ptr<Node> sourceNode,
                                                      const Address& remoteAddress,
                                                      const NetworkSceneTrafficPattern& pattern,
                                                      uint32_t packetSize,
                                                      Time startTime,
                                                      Time stopTime);
bool UpdateNetworkSceneTrafficSourceRate(Ptr<Application> application,
                                         const NetworkSceneTrafficPattern& basePattern,
                                         double rateMultiplier,
                                         uint32_t packetSize);

} // namespace ns3

#endif /* NETWORK_SCENE_TRAFFIC_H */
