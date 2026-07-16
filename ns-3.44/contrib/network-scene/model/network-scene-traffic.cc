#include "network-scene-traffic.h"

#include "ns3/address.h"
#include "ns3/application.h"
#include "ns3/data-rate.h"
#include "ns3/double.h"
#include "ns3/inet-socket-address.h"
#include "ns3/log.h"
#include "ns3/on-off-helper.h"
#include "ns3/onoff-application.h"
#include "ns3/packet.h"
#include "ns3/random-variable-stream.h"
#include "ns3/simulator.h"
#include "ns3/socket.h"
#include "ns3/string.h"
#include "ns3/uinteger.h"
#include "ns3/udp-socket-factory.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("NetworkSceneTraffic");

namespace
{

uint64_t
MbpsToBps(double mbps)
{
    if (mbps <= 0.0)
    {
        return 1;
    }
    return static_cast<uint64_t>(std::llround(mbps * 1000000.0));
}

std::string
ConstantRv(double value)
{
    return "ns3::ConstantRandomVariable[Constant=" + std::to_string(value) + "]";
}

std::string
ExponentialRv(double mean)
{
    return "ns3::ExponentialRandomVariable[Mean=" + std::to_string(std::max(0.000001, mean)) + "]";
}

class PoissonUdpApplication : public Application
{
  public:
    void Setup(const Address& remoteAddress, const NetworkSceneTrafficPattern& pattern, uint32_t packetSize)
    {
        m_remoteAddress = remoteAddress;
        m_packetSize = std::max(1u, packetSize);
        if (pattern.paramLambda > 0.0)
        {
            m_meanIntervalSeconds = std::max(0.000001, 1.0 / pattern.paramLambda);
            return;
        }

        const double bitsPerPacket = static_cast<double>(m_packetSize) * 8.0;
        const double bps = static_cast<double>(MbpsToBps(pattern.demandMbps));
        m_meanIntervalSeconds = std::max(0.000001, bitsPerPacket / bps);
    }

    void SetRateMultiplier(const NetworkSceneTrafficPattern& pattern, double rateMultiplier)
    {
        rateMultiplier = std::max(0.000001, rateMultiplier);
        if (pattern.paramLambda > 0.0)
        {
            m_meanIntervalSeconds = std::max(0.000001, 1.0 / (pattern.paramLambda * rateMultiplier));
            if (m_interval)
            {
                m_interval->SetAttribute("Mean", DoubleValue(m_meanIntervalSeconds));
            }
            return;
        }

        const double bitsPerPacket = static_cast<double>(m_packetSize) * 8.0;
        const double bps = static_cast<double>(MbpsToBps(pattern.demandMbps * rateMultiplier));
        m_meanIntervalSeconds = std::max(0.000001, bitsPerPacket / bps);
        if (m_interval)
        {
            m_interval->SetAttribute("Mean", DoubleValue(m_meanIntervalSeconds));
        }
    }

  private:
    void StartApplication() override
    {
        m_running = true;
        m_socket = Socket::CreateSocket(GetNode(), UdpSocketFactory::GetTypeId());
        m_socket->Connect(m_remoteAddress);
        m_interval = CreateObject<ExponentialRandomVariable>();
        m_interval->SetAttribute("Mean", DoubleValue(m_meanIntervalSeconds));
        ScheduleNext();
    }

    void StopApplication() override
    {
        m_running = false;
        if (m_sendEvent.IsPending())
        {
            Simulator::Cancel(m_sendEvent);
        }
        if (m_socket)
        {
            m_socket->Close();
        }
    }

    void SendPacket()
    {
        if (!m_running || !m_socket)
        {
            return;
        }
        m_socket->Send(Create<Packet>(m_packetSize));
        ScheduleNext();
    }

    void ScheduleNext()
    {
        if (!m_running)
        {
            return;
        }
        m_sendEvent = Simulator::Schedule(Seconds(m_interval->GetValue()), &PoissonUdpApplication::SendPacket, this);
    }

    Ptr<Socket> m_socket;
    Ptr<ExponentialRandomVariable> m_interval;
    EventId m_sendEvent;
    Address m_remoteAddress;
    uint32_t m_packetSize{1024};
    double m_meanIntervalSeconds{0.001};
    bool m_running{false};
};

} // namespace

ApplicationContainer
InstallNetworkSceneTrafficSource(Ptr<Node> sourceNode,
                                 const Address& remoteAddress,
                                 const NetworkSceneTrafficPattern& pattern,
                                 uint32_t packetSize,
                                 Time startTime,
                                 Time stopTime)
{
    if (pattern.featureModel == "poisson")
    {
        Ptr<PoissonUdpApplication> app = CreateObject<PoissonUdpApplication>();
        app->Setup(remoteAddress, pattern, packetSize);
        sourceNode->AddApplication(app);
        app->SetStartTime(startTime);
        app->SetStopTime(stopTime);
        return ApplicationContainer(app);
    }

    OnOffHelper onoff("ns3::UdpSocketFactory", remoteAddress);
    if (pattern.featureModel == "on_off")
    {
        const double peakMbps = pattern.paramPeakRateMbps > 0.0 ? pattern.paramPeakRateMbps : pattern.demandMbps;
        onoff.SetAttribute("OnTime", StringValue(ExponentialRv(pattern.paramOnMean > 0.0 ? pattern.paramOnMean : 1.0)));
        onoff.SetAttribute("OffTime", StringValue(ExponentialRv(pattern.paramOffMean > 0.0 ? pattern.paramOffMean : 1.0)));
        onoff.SetAttribute("DataRate", DataRateValue(DataRate(MbpsToBps(peakMbps))));
        onoff.SetAttribute("PacketSize", UintegerValue(packetSize));
    }
    else
    {
        onoff.SetAttribute("OnTime", StringValue(ConstantRv(1.0)));
        onoff.SetAttribute("OffTime", StringValue(ConstantRv(0.0)));
        onoff.SetAttribute("DataRate", DataRateValue(DataRate(MbpsToBps(pattern.demandMbps))));
        onoff.SetAttribute("PacketSize", UintegerValue(packetSize));
    }

    ApplicationContainer source = onoff.Install(sourceNode);
    source.Start(startTime);
    source.Stop(stopTime);
    return source;
}

bool
UpdateNetworkSceneTrafficSourceRate(Ptr<Application> application,
                                    const NetworkSceneTrafficPattern& basePattern,
                                    double rateMultiplier,
                                    uint32_t packetSize)
{
    if (!application)
    {
        return false;
    }

    rateMultiplier = std::max(0.000001, rateMultiplier);
    if (basePattern.featureModel == "poisson")
    {
        Ptr<PoissonUdpApplication> poisson = DynamicCast<PoissonUdpApplication>(application);
        if (!poisson)
        {
            return false;
        }
        poisson->SetRateMultiplier(basePattern, rateMultiplier);
        return true;
    }

    const double baseRateMbps =
        basePattern.featureModel == "on_off" && basePattern.paramPeakRateMbps > 0.0
            ? basePattern.paramPeakRateMbps
            : basePattern.demandMbps;
    application->SetAttribute("DataRate", DataRateValue(DataRate(MbpsToBps(baseRateMbps * rateMultiplier))));
    application->SetAttribute("PacketSize", UintegerValue(packetSize));
    return true;
}

} // namespace ns3
