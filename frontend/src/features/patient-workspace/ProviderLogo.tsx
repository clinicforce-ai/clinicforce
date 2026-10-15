const providerLogos = {
  google: {
    src: 'https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg',
    alt: 'Google',
  },
  mychart: {
    src: 'https://media.epic.com/mychartdotorg/site/en-us/images/logo/site-logo.svg',
    alt: 'MyChart',
  },
  ecw: {
    src: 'https://www.eclinicalworks.com/wp-content/uploads/2014/08/main-logo.png',
    alt: 'eClinicalWorks',
  },
  healow: {
    src: 'https://plus.healow.com/wp-content/uploads/2022/03/healow-logo@2x.png',
    alt: 'healow',
  },
} as const

export function ProviderLogo({ provider }: { provider: keyof typeof providerLogos }) {
  const logo = providerLogos[provider]
  return <img className={`provider-logo provider-logo-${provider}`} src={logo.src} alt={logo.alt} />
}
